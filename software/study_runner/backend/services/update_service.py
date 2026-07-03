from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import requests

from study_runner.update_keys import TRUSTED_UPDATE_PUBLIC_KEYS
from study_runner.version import __version__


DEFAULT_MANIFEST_URL = (
    "https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest/download/"
    "study-runner-python-latest.json"
)
UPDATER_SCHEMA_VERSION = 1
STATE_FILE_NAME = "update-state.json"
MANIFEST_TIMEOUT_SECONDS = 15
DOWNLOAD_TIMEOUT_SECONDS = 60


class UpdateError(Exception):
    """Raised when update state, metadata, or downloaded assets are invalid."""


@dataclass(frozen=True)
class UpdatePaths:
    root: Path
    downloads: Path
    staged: Path
    state_file: Path


def build_update_status(app_config: dict[str, Any]) -> dict[str, Any]:
    paths = resolve_update_paths(app_config)
    key_status = get_public_key_status()
    state = _read_state(paths.state_file)
    status = _base_status(app_config, key_status)
    status.update(
        {
            "state": state.get("state", "idle"),
            "checked_at": state.get("checked_at", ""),
            "error": state.get("error", ""),
            "update": state.get("update"),
            "download": state.get("download"),
            "staged": _public_staged(state.get("staged")),
            "install_supported": is_install_supported(app_config),
        }
    )
    return status


def check_for_update(app_config: dict[str, Any]) -> dict[str, Any]:
    _require_public_key()
    paths = resolve_update_paths(app_config)
    manifest_url = get_manifest_url()
    manifest = fetch_manifest(manifest_url)
    platform_key = detect_platform_key()
    asset = select_platform_asset(manifest, platform_key)
    latest_version = str(manifest["version"])
    available = compare_versions(latest_version, __version__) > 0

    state = {
        "state": "available" if available else "current",
        "checked_at": _utc_now(),
        "current_version": __version__,
        "platform": platform_key,
        "manifest_url": manifest_url,
        "update": {
            "available": available,
            "version": latest_version,
            "notes_url": str(manifest.get("notes_url") or ""),
            "asset": _public_asset(asset),
        },
        "asset": asset,
        "error": "",
    }
    _write_state(paths.state_file, state)
    return build_update_status(app_config)


def download_and_stage_update(app_config: dict[str, Any]) -> dict[str, Any]:
    _require_public_key()
    paths = resolve_update_paths(app_config)
    state = _read_state(paths.state_file)
    update_info = state.get("update") if isinstance(state.get("update"), dict) else {}
    if not update_info.get("available"):
        raise UpdateError("No checked update is available. Check for updates first.")

    asset = state.get("asset") if isinstance(state.get("asset"), dict) else None
    if not asset:
        raise UpdateError("The checked update has no downloadable asset.")

    version = str(update_info.get("version") or "")
    platform_key = str(state.get("platform") or detect_platform_key())
    file_name = _asset_file_name(asset)
    download_path = paths.downloads / file_name

    paths.downloads.mkdir(parents=True, exist_ok=True)
    paths.staged.mkdir(parents=True, exist_ok=True)

    _set_download_state(
        paths.state_file,
        state,
        {
            "state": "downloading",
            "file_name": file_name,
            "bytes_downloaded": 0,
            "total_bytes": int(asset.get("size") or 0),
        },
    )
    sha256 = _download_asset(asset, download_path, paths.state_file, state)

    expected_sha256 = str(asset.get("sha256") or "").strip().lower()
    if sha256 != expected_sha256:
        _set_error(paths.state_file, state, f"Downloaded file hash mismatch for {file_name}.")
        raise UpdateError("Downloaded update did not match the expected SHA-256 hash.")

    verify_asset_signature(version, platform_key, asset)
    _set_download_state(
        paths.state_file,
        state,
        {
            "state": "verifying",
            "file_name": file_name,
            "bytes_downloaded": download_path.stat().st_size,
            "total_bytes": download_path.stat().st_size,
            "sha256": sha256,
        },
    )

    staged_info = _stage_zip(download_path, paths.staged, version)
    state = _read_state(paths.state_file)
    state.update(
        {
            "state": "staged",
            "error": "",
            "download": {
                "state": "staged",
                "file_name": file_name,
                "bytes_downloaded": download_path.stat().st_size,
                "total_bytes": download_path.stat().st_size,
                "sha256": sha256,
            },
            "staged": staged_info,
        }
    )
    _write_state(paths.state_file, state)
    return build_update_status(app_config)


def request_update_install(app_config: dict[str, Any]) -> dict[str, Any]:
    if not is_install_supported(app_config):
        raise UpdateError("Install/restart is only available in Python packaged builds, not source mode or legacy desktop mode.")

    paths = resolve_update_paths(app_config)
    state = _read_state(paths.state_file)
    staged = state.get("staged") if isinstance(state.get("staged"), dict) else None
    if not staged:
        raise UpdateError("No staged update is ready to install.")
    executable = Path(str(staged.get("executable") or ""))
    if not executable.exists():
        raise UpdateError("The staged update executable is missing.")

    helper = {
        "storage_root": str(app_config.get("STORAGE_ROOT", "")),
        "host": str(app_config.get("SERVER_HOST", "")),
        "port": str(app_config.get("SERVER_PORT", "")),
        "https": os.getenv("STUDY_RUNNER_HTTPS", ""),
    }
    state["state"] = "installing"
    state["helper"] = helper
    state["install_requested_at"] = _utc_now()
    state["error"] = ""
    _write_state(paths.state_file, state)
    _spawn_update_helper(paths.state_file, app_config)
    return build_update_status(app_config)


def resolve_update_paths(app_config: dict[str, Any]) -> UpdatePaths:
    storage_root = Path(app_config.get("STORAGE_ROOT") or app_config.get("DATA_DIR") or ".").expanduser().resolve()
    root = storage_root / "updates"
    return UpdatePaths(
        root=root,
        downloads=root / "downloads",
        staged=root / "staged",
        state_file=root / STATE_FILE_NAME,
    )


def get_manifest_url() -> str:
    return os.getenv("STUDY_RUNNER_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL).strip() or DEFAULT_MANIFEST_URL


def get_public_key_status() -> dict[str, Any]:
    try:
        keys = load_public_keys()
    except UpdateError as error:
        return {"configured": False, "error": str(error)}
    return {"configured": bool(keys), "error": ""}


def load_public_keys() -> list[Ed25519PublicKey]:
    raw_values: list[str] = []
    env_key = os.getenv("STUDY_RUNNER_UPDATE_PUBLIC_KEY", "").strip()
    if env_key:
        raw_values.append(env_key)
    raw_values.extend(str(value).strip() for value in TRUSTED_UPDATE_PUBLIC_KEYS if str(value).strip())

    keys: list[Ed25519PublicKey] = []
    for raw_value in raw_values:
        keys.append(_load_public_key(raw_value))
    return keys


def fetch_manifest(manifest_url: str) -> dict[str, Any]:
    try:
        response = requests.get(manifest_url, timeout=MANIFEST_TIMEOUT_SECONDS, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise UpdateError(f"Could not fetch update manifest: {error}") from error
    except ValueError as error:
        raise UpdateError("Update manifest is not valid JSON.") from error
    return normalize_manifest(payload)


def normalize_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpdateError("Update manifest must be a JSON object.")

    version = str(payload.get("version") or "").strip()
    if not _is_semver(version):
        raise UpdateError("Update manifest has no valid version.")

    minimum_updater_version = int(payload.get("minimum_updater_version") or 1)
    if minimum_updater_version > UPDATER_SCHEMA_VERSION:
        raise UpdateError("This update requires a newer Study Runner updater.")

    assets = payload.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise UpdateError("Update manifest has no platform assets.")

    normalized_assets: dict[str, dict[str, Any]] = {}
    for platform_key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        normalized_assets[str(platform_key)] = normalize_asset(asset)
    if not normalized_assets:
        raise UpdateError("Update manifest has no usable platform assets.")

    return {
        "version": version,
        "notes_url": str(payload.get("notes_url") or ""),
        "minimum_updater_version": minimum_updater_version,
        "assets": normalized_assets,
    }


def normalize_asset(asset: dict[str, Any]) -> dict[str, Any]:
    url = str(asset.get("url") or "").strip()
    sha256 = str(asset.get("sha256") or "").strip().lower()
    signature = str(asset.get("signature") or "").strip()
    if not url.startswith(("https://", "http://")):
        raise UpdateError("Update asset has no valid URL.")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise UpdateError("Update asset has no valid SHA-256 hash.")
    if not signature:
        raise UpdateError("Update asset has no signature.")
    size = int(asset.get("size") or 0)
    return {
        "url": url,
        "sha256": sha256,
        "signature": signature,
        "size": size,
        "file_name": str(asset.get("file_name") or "").strip(),
    }


def select_platform_asset(manifest: dict[str, Any], platform_key: str | None = None) -> dict[str, Any]:
    key = platform_key or detect_platform_key()
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
    asset = assets.get(key)
    if not asset:
        raise UpdateError(f"No update asset is available for {key}.")
    return dict(asset)


def detect_platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        arch = machine or "unknown"

    if system == "windows":
        os_key = "windows"
    elif system == "darwin":
        os_key = "macos"
    elif system == "linux":
        os_key = "linux"
    else:
        os_key = system or "unknown"
    return f"{os_key}-{arch}"


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_tuple(left)
    right_parts = _version_tuple(right)
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def canonical_asset_payload(version: str, platform_key: str, asset: dict[str, Any]) -> bytes:
    payload = {
        "schema": UPDATER_SCHEMA_VERSION,
        "version": str(version),
        "platform": str(platform_key),
        "url": str(asset.get("url") or ""),
        "sha256": str(asset.get("sha256") or "").lower(),
        "size": int(asset.get("size") or 0),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_asset_signature(version: str, platform_key: str, asset: dict[str, Any]) -> None:
    signature = _decode_base64(str(asset.get("signature") or ""))
    payload = canonical_asset_payload(version, platform_key, asset)
    for public_key in load_public_keys():
        try:
            public_key.verify(signature, payload)
            return
        except InvalidSignature:
            continue
    raise UpdateError("Update asset signature could not be verified.")


def is_install_supported(app_config: dict[str, Any]) -> bool:
    app_mode = str(app_config.get("APP_MODE") or "").strip().lower()
    return bool(getattr(sys, "frozen", False) and app_mode != "desktop")


def _base_status(app_config: dict[str, Any], key_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "current_version": __version__,
        "platform": detect_platform_key(),
        "manifest_url": get_manifest_url(),
        "app_mode": str(app_config.get("APP_MODE") or ""),
        "packaged": bool(getattr(sys, "frozen", False)),
        "configured": bool(key_status.get("configured")),
        "configuration_error": str(key_status.get("error") or ""),
    }


def _require_public_key() -> None:
    status = get_public_key_status()
    if not status.get("configured"):
        message = status.get("error") or "No Python updater public key is configured."
        raise UpdateError(str(message))


def _load_public_key(raw_value: str) -> Ed25519PublicKey:
    raw_value = raw_value.strip()
    try:
        if "BEGIN PUBLIC KEY" in raw_value:
            key = serialization.load_pem_public_key(raw_value.encode("utf-8"))
            if isinstance(key, Ed25519PublicKey):
                return key
            raise UpdateError("Configured update public key is not an Ed25519 key.")

        key_bytes = _decode_base64(raw_value)
        if len(key_bytes) != 32:
            raise UpdateError("Configured update public key must be a 32-byte base64 Ed25519 key.")
        return Ed25519PublicKey.from_public_bytes(key_bytes)
    except ValueError as error:
        raise UpdateError("Configured update public key is not valid base64 or PEM.") from error


def _download_asset(asset: dict[str, Any], destination: Path, state_file: Path, state: dict[str, Any]) -> str:
    hasher = hashlib.sha256()
    bytes_downloaded = 0
    total_bytes = int(asset.get("size") or 0)
    last_state_update = 0.0

    try:
        response = requests.get(asset["url"], stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
        if not total_bytes:
            total_bytes = int(response.headers.get("content-length") or 0)

        with destination.open("wb") as file_handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                file_handle.write(chunk)
                hasher.update(chunk)
                bytes_downloaded += len(chunk)
                now = time.monotonic()
                if now - last_state_update > 0.5:
                    _set_download_state(
                        state_file,
                        state,
                        {
                            "state": "downloading",
                            "file_name": destination.name,
                            "bytes_downloaded": bytes_downloaded,
                            "total_bytes": total_bytes,
                        },
                    )
                    last_state_update = now
    except requests.RequestException as error:
        _set_error(state_file, state, f"Could not download update: {error}")
        raise UpdateError(f"Could not download update: {error}") from error
    finally:
        close = getattr(locals().get("response", None), "close", None)
        if callable(close):
            close()

    return hasher.hexdigest()


def _stage_zip(zip_path: Path, staged_root: Path, version: str) -> dict[str, Any]:
    target = (staged_root / version).resolve()
    temp_target = (staged_root / f".{version}.tmp").resolve()
    if temp_target.exists():
        shutil.rmtree(temp_target)
    if target.exists():
        shutil.rmtree(target)
    temp_target.mkdir(parents=True, exist_ok=True)

    try:
        _safe_extract_zip(zip_path, temp_target)
        executable = _find_staged_executable(temp_target)
        if os.name != "nt":
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        temp_target.replace(target)
        executable = target / executable.relative_to(temp_target)
        return {
            "version": version,
            "path": str(target),
            "executable": str(executable),
            "staged_at": _utc_now(),
        }
    except Exception:
        if temp_target.exists():
            shutil.rmtree(temp_target)
        raise


def _safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination != target and destination not in target.parents:
                raise UpdateError("Update archive contains an unsafe path.")
        archive.extractall(destination)


def _find_staged_executable(stage_dir: Path) -> Path:
    executable_name = "study-runner-server.exe" if os.name == "nt" else "study-runner-server"
    candidates = sorted(
        (path for path in stage_dir.rglob(executable_name) if path.is_file()),
        key=lambda path: (len(path.parts), str(path)),
    )
    if not candidates:
        raise UpdateError(f"Staged update does not contain {executable_name}.")
    return candidates[0]


def _spawn_update_helper(state_file: Path, app_config: dict[str, Any]) -> None:
    base_dir = Path(app_config.get("BASE_DIR") or ".")
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--apply-update", str(state_file)]
    else:
        cmd = [sys.executable, "-m", "study_runner.update_helper", str(state_file)]

    kwargs: dict[str, Any] = {"cwd": str(base_dir), "env": os.environ.copy(), "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def _public_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": str(asset.get("url") or ""),
        "sha256": str(asset.get("sha256") or ""),
        "size": int(asset.get("size") or 0),
        "file_name": _asset_file_name(asset),
    }


def _public_staged(staged: Any) -> dict[str, Any] | None:
    if not isinstance(staged, dict):
        return None
    return {
        "version": str(staged.get("version") or ""),
        "path": str(staged.get("path") or ""),
        "staged_at": str(staged.get("staged_at") or ""),
    }


def _asset_file_name(asset: dict[str, Any]) -> str:
    explicit = str(asset.get("file_name") or "").strip()
    if explicit:
        return Path(explicit).name
    parsed = urlparse(str(asset.get("url") or ""))
    return Path(parsed.path).name or "study-runner-update.zip"


def _set_download_state(state_file: Path, state: dict[str, Any], download: dict[str, Any]) -> None:
    state["state"] = str(download.get("state") or "downloading")
    state["download"] = download
    state["error"] = ""
    _write_state(state_file, state)


def _set_error(state_file: Path, state: dict[str, Any], message: str) -> None:
    state["state"] = "error"
    state["error"] = message
    _write_state(state_file, state)


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, sort_keys=True)
        file_handle.write("\n")


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not _is_semver(value):
        return (0, 0, 0)
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _is_semver(value: str) -> bool:
    parts = str(value).split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def _decode_base64(value: str) -> bytes:
    padded = value.strip()
    padded += "=" * (-len(padded) % 4)
    return base64.b64decode(padded.encode("ascii"), validate=False)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
