from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import shutil
import socket
from typing import Any

# Moved to shared/runtime_mode.py so plugins and plugin_framework can depend
# on them without depending on the server package (docs/archive/architecture-1.0-umbau.md, Phase
# 2.1). Imported here both for this module's own internal use below and as a
# re-export for runtime callers (app_server.py, results_service.py,
# trial_service.py, ...). Keep this
# import; do not reintroduce the definitions here.
from study_runner.shared.runtime_mode import get_app_mode, get_project_base_dir, is_frozen  # noqa: F401


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 3000
DEFAULT_CERTIFICATE_DOWNLOAD_PORT = 3002


@dataclass(frozen=True)
class RuntimePaths:
    base_dir: Path
    content_dir: Path
    storage_root: Path
    settings_dir: Path
    config_file: Path
    hardware_config_file: Path
    data_dir: Path
    saved_studies_dir: Path
    local_secrets_file: Path
    branding_dir: Path
    uses_external_storage: bool


def read_server_host() -> str:
    return os.getenv("STUDY_RUNNER_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST


def read_server_port() -> int:
    raw_value = os.getenv("STUDY_RUNNER_PORT", "").strip()
    if not raw_value:
        return DEFAULT_PORT
    try:
        port = int(raw_value)
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def read_certificate_download_port() -> int:
    raw_value = os.getenv("STUDY_RUNNER_CERTIFICATE_DOWNLOAD_PORT", "").strip()
    if not raw_value:
        return DEFAULT_CERTIFICATE_DOWNLOAD_PORT
    try:
        port = int(raw_value)
    except ValueError:
        return DEFAULT_CERTIFICATE_DOWNLOAD_PORT
    return port if 1 <= port <= 65535 else DEFAULT_CERTIFICATE_DOWNLOAD_PORT


def is_background_disabled() -> bool:
    return os.getenv("STUDY_RUNNER_DISABLE_BACKGROUND", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_https_enabled() -> bool:
    return os.getenv("STUDY_RUNNER_HTTPS", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_server_scheme() -> str:
    return "https" if is_https_enabled() else "http"


def resolve_runtime_paths(base_dir: Path | None = None) -> RuntimePaths:
    resource_base = Path(base_dir or get_project_base_dir()).resolve()
    content_override = os.getenv("STUDY_RUNNER_CONTENT_DIR", "").strip()
    external_data_dir = os.getenv("STUDY_RUNNER_DATA_DIR", "").strip()
    content_dir = Path(content_override).expanduser().resolve() if content_override else resource_base / "study_content"

    if external_data_dir:
        storage_root = Path(external_data_dir).expanduser().resolve()
        settings_root = storage_root
        data_root = storage_root
        uses_external_storage = True
    else:
        storage_root = resource_base
        settings_root = content_dir
        data_root = resource_base
        uses_external_storage = False

    settings_dir = settings_root / "settings"
    return RuntimePaths(
        base_dir=resource_base,
        content_dir=content_dir,
        storage_root=storage_root,
        settings_dir=settings_dir,
        config_file=settings_dir / "study_config.json",
        hardware_config_file=settings_dir / "hardware_settings.json",
        data_dir=data_root / "saved_results",
        saved_studies_dir=settings_root / "studies",
        local_secrets_file=settings_dir / "local_secrets.json",
        branding_dir=settings_dir / "branding",
        uses_external_storage=uses_external_storage,
    )


def initialize_runtime_storage(paths: RuntimePaths) -> None:
    """Create writable runtime folders and seed external storage with default files."""
    paths.settings_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.saved_studies_dir.mkdir(parents=True, exist_ok=True)
    paths.branding_dir.mkdir(parents=True, exist_ok=True)

    if not paths.uses_external_storage:
        return

    default_settings = paths.content_dir / "settings"
    _copy_default_file(default_settings / "study_config.json", paths.config_file)
    _copy_default_file(default_settings / "hardware_settings.json", paths.hardware_config_file)
    _copy_default_studies(paths.content_dir / "studies", paths.saved_studies_dir)
    _copy_demo_results(paths.base_dir / "saved_results", paths.data_dir)


def get_local_private_ips() -> list[str]:
    """Return likely LAN IPv4 addresses for participant devices."""
    candidates: list[str] = []
    hostnames = {socket.gethostname(), socket.getfqdn()}

    for hostname in hostnames:
        try:
            for result in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip_address = result[4][0]
                if ip_address not in candidates:
                    candidates.append(ip_address)
        except OSError:
            continue

    private_ips = [ip for ip in candidates if _is_private_lan_ip(ip)]
    if private_ips:
        return private_ips

    public_non_loopback = [ip for ip in candidates if not _is_loopback_ip(ip)]
    if public_non_loopback:
        return public_non_loopback

    return ["127.0.0.1"]


def build_runtime_info(app_config: dict[str, Any], scheme: str | None = None) -> dict[str, Any]:
    active_scheme = scheme or get_server_scheme()
    port = int(app_config.get("SERVER_PORT") or read_server_port())
    local_ips = get_local_private_ips()
    participant_urls = [_format_url(active_scheme, ip_address, port, "") for ip_address in local_ips]

    return {
        "ok": True,
        "app_mode": app_config.get("APP_MODE") or get_app_mode(),
        "host": app_config.get("SERVER_HOST") or read_server_host(),
        "port": port,
        "scheme": active_scheme,
        "admin_url": _format_url(active_scheme, "localhost", port, "/admin"),
        "study_url": _format_url(active_scheme, "localhost", port, ""),
        "participant_url": participant_urls[0],
        "participant_urls": participant_urls,
        "local_ips": local_ips,
        "data_dir": str(app_config.get("DATA_DIR", "")),
        "content_dir": str(app_config.get("CONTENT_DIR", "")),
        "settings_dir": str(app_config.get("SETTINGS_DIR", "")),
        "uses_external_storage": bool(app_config.get("USES_EXTERNAL_STORAGE", False)),
        "https_certificate": app_config.get("HTTPS_CERTIFICATE", {}),
    }


def _copy_default_file(source: Path, destination: Path) -> None:
    if destination.exists() or not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_default_studies(source_dir: Path, destination_dir: Path) -> None:
    if not source_dir.exists() or any(destination_dir.iterdir()):
        return

    for source_file in source_dir.iterdir():
        if source_file.is_file():
            shutil.copy2(source_file, destination_dir / source_file.name)


# The one curated demo session the project ships (see .gitignore). Seeded only
# into a brand-new external data folder, never over real results.
DEMO_RESULT_DIRECTORY = "Demo_Completed_Study"


def _copy_demo_results(source_root: Path, destination_dir: Path) -> None:
    source = source_root / DEMO_RESULT_DIRECTORY
    if not source.is_dir() or any(destination_dir.iterdir()):
        return
    shutil.copytree(source, destination_dir / DEMO_RESULT_DIRECTORY)


def _format_url(scheme: str, host: str, port: int, path: str) -> str:
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}{path}"
    return f"{scheme}://{host}:{port}{path}"


def _is_private_lan_ip(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(parsed.version == 4 and parsed.is_private and not parsed.is_loopback)


def _is_loopback_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False
