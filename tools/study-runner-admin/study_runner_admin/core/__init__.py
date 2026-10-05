from __future__ import annotations

import gzip
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO_OWNER = "realfabianschmidt"
REPO_NAME = "MRG-StudyRunner"
GITHUB_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"


def _http_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "study-runner-admin"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def list_available_releases(limit: int = 10) -> list[str]:
    payload = _http_json(f"{GITHUB_API}/releases")
    tags = [release.get("tag_name") for release in payload if isinstance(release, dict)]
    tags = [tag for tag in tags if isinstance(tag, str) and tag]
    return tags[:limit]


def resolve_release(version: str | None = None) -> dict[str, Any]:
    if version:
        normalized = version.strip()
        if normalized.startswith("v"):
            url = f"{GITHUB_API}/releases/tags/{normalized}"
        else:
            url = f"{GITHUB_API}/releases/tags/v{normalized}"
        payload = _http_json(url)
    else:
        payload = _http_json(f"{GITHUB_API}/releases/latest")

    if not isinstance(payload, dict):
        raise RuntimeError("GitHub release metadata could not be parsed.")
    assets = payload.get("assets") or []
    source_asset = None
    for asset in assets:
        name = str(asset.get("name") or "")
        if "study-runner-source" in name:
            source_asset = asset
            break
    if source_asset is None:
        raise RuntimeError(f"No source release asset found for release {payload.get('tag_name')!r}.")

    return {
        "tag_name": payload.get("tag_name") or "unknown",
        "name": payload.get("name") or payload.get("tag_name") or "Study Runner",
        "download_url": source_asset.get("browser_download_url"),
        "asset_name": source_asset.get("name"),
    }


def _download_file(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "study-runner-admin"})
    with urllib.request.urlopen(request, timeout=180) as response:
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    return destination


def _extract_archive(archive_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = archive_path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(target_dir)
        return
    if suffix == ".gz" and archive_path.name.endswith(".tar.gz"):
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(target_dir)
        return
    raise RuntimeError(f"Unsupported archive type for {archive_path.name}")


def install_release(
    install_root: Path,
    version: str | None = None,
    data_dir: Path | None = None,
    overwrite: bool = False,
    keep_data: bool = False,
) -> dict[str, Any]:
    install_root = install_root.expanduser().resolve()
    release = resolve_release(version)
    archive_url = release["download_url"]
    if not archive_url:
        raise RuntimeError(f"Release {release['tag_name']} does not include a downloadable source archive.")

    if install_root.exists() and not overwrite:
        if any(install_root.iterdir()):
            raise FileExistsError(f"Installation directory already exists: {install_root}. Use --overwrite to replace it.")

    if install_root.exists() and overwrite:
        shutil.rmtree(install_root)

    install_root.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="study-runner-admin-"))
    archive_path = tmp / release["asset_name"]
    try:
        _download_file(archive_url, archive_path)
        _extract_archive(archive_path, install_root)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if data_dir is not None:
        data_dir = data_dir.expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        config_file = install_root / "study-runner-data-dir.json"
        config_file.write_text(json.dumps({"data_dir": str(data_dir)}, indent=2), encoding="utf-8")

    state = {
        "installed_version": release["tag_name"],
        "install_root": str(install_root),
        "data_dir": str(data_dir) if data_dir is not None else None,
        "keep_data": keep_data,
        "source_asset": release["asset_name"],
    }
    (install_root / "study-runner-admin-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def repair_installation(install_root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    install_root = install_root.expanduser().resolve()
    if not install_root.exists():
        raise FileNotFoundError(f"Installation directory does not exist: {install_root}")

    state_file = install_root / "study-runner-admin-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    if data_dir is not None:
        data_dir = data_dir.expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        state["data_dir"] = str(data_dir)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    return {"status": "repaired", "install_root": str(install_root), "state": state}


def remove_installation(install_root: Path, force: bool = False) -> dict[str, Any]:
    install_root = install_root.expanduser().resolve()
    if not install_root.exists():
        raise FileNotFoundError(f"Installation directory does not exist: {install_root}")
    if not force:
        answer = input(f"Delete {install_root}? This action is irreversible. [y/N]: ")
        if answer.strip().lower() not in {"y", "yes"}:
            return {"status": "cancelled", "install_root": str(install_root)}
    shutil.rmtree(install_root)
    return {"status": "removed", "install_root": str(install_root)}
