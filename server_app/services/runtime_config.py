from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import sys
from typing import Any


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 3000


@dataclass(frozen=True)
class RuntimePaths:
    base_dir: Path
    storage_root: Path
    settings_dir: Path
    config_file: Path
    hardware_config_file: Path
    data_dir: Path
    saved_studies_dir: Path
    local_secrets_file: Path
    uses_external_storage: bool


def get_project_base_dir() -> Path:
    """Return the folder that contains bundled project resources."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    return Path(__file__).resolve().parents[2]


def get_app_mode() -> str:
    return os.getenv("STUDY_RUNNER_APP_MODE", "python").strip().lower() or "python"


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


def is_https_enabled() -> bool:
    return os.getenv("STUDY_RUNNER_HTTPS", "").strip().lower() in {"1", "true", "yes", "on"}


def get_server_scheme() -> str:
    return "https" if is_https_enabled() else "http"


def resolve_runtime_paths(base_dir: Path | None = None) -> RuntimePaths:
    resource_base = Path(base_dir or get_project_base_dir()).resolve()
    external_data_dir = os.getenv("STUDY_RUNNER_DATA_DIR", "").strip()

    if external_data_dir:
        storage_root = Path(external_data_dir).expanduser().resolve()
        uses_external_storage = True
    else:
        storage_root = resource_base
        uses_external_storage = False

    settings_dir = storage_root / "settings"
    return RuntimePaths(
        base_dir=resource_base,
        storage_root=storage_root,
        settings_dir=settings_dir,
        config_file=settings_dir / "study_config.json",
        hardware_config_file=settings_dir / "hardware_settings.json",
        data_dir=storage_root / "saved_results",
        saved_studies_dir=storage_root / "saved_studies",
        local_secrets_file=settings_dir / "local_secrets.json",
        uses_external_storage=uses_external_storage,
    )


def initialize_runtime_storage(paths: RuntimePaths) -> None:
    """Create writable runtime folders and seed desktop storage with default files."""
    paths.settings_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.saved_studies_dir.mkdir(parents=True, exist_ok=True)

    if not paths.uses_external_storage:
        return

    default_settings = paths.base_dir / "settings"
    _copy_default_file(default_settings / "study_config.json", paths.config_file)
    _copy_default_file(default_settings / "hardware_settings.json", paths.hardware_config_file)
    _copy_default_studies(paths.base_dir / "saved_studies", paths.saved_studies_dir)


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
        "settings_dir": str(app_config.get("SETTINGS_DIR", "")),
        "uses_external_storage": bool(app_config.get("USES_EXTERNAL_STORAGE", False)),
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
