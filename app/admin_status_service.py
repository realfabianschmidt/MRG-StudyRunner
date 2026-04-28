import json
import os
import sys
from pathlib import Path
from typing import Any

from .integrations import camera_affect_adapter, mini_radar_adapter, raspi_adapter
from .study_client_service import get_client_status


def build_admin_status(base_dir: Path, hardware_config: dict[str, Any]) -> dict[str, Any]:
    """Build one small status payload for the admin dashboard."""
    brainbit_status = _build_brainbit_status(base_dir, hardware_config.get("brainbit", {}))
    mini_radar_config = hardware_config.get("mini_radar") or hardware_config.get("radar") or {}
    camera_config = hardware_config.get("camera_emotion") or hardware_config.get("camera") or {}

    return {
        "ok": True,
        "study_clients": get_client_status(),
        "integrations": {
            "lsl": _enabled_status(hardware_config.get("lsl", {})),
            "osc": _enabled_status(hardware_config.get("osc", {})),
            "brainbit": brainbit_status,
            "mini_radar": _merge_config_status(mini_radar_config, mini_radar_adapter.get_status()),
            "camera_emotion": _merge_config_status(camera_config, camera_affect_adapter.get_status()),
            "labrecorder": _enabled_status(hardware_config.get("labrecorder", {})),
            "raspi": raspi_adapter.get_status(hardware_config.get("raspi", {})),
        },
        "timestamp_strategy": {
            "primary": "LSL",
            "recording_format": ".xdf",
            "note": "Use LSL timestamps as the primary synchronization layer and keep source timestamps as metadata.",
        },
    }


def _build_brainbit_status(base_dir: Path, brainbit_config: dict[str, Any]) -> dict[str, Any]:
    enabled_state = _enabled_status(brainbit_config)
    log_dir = _resolve_project_path(
        base_dir,
        _resolve_platform_value(brainbit_config.get("log_dir")) or "brainbit/logs",
    )
    state_path = log_dir / "brainbit_state.json"

    state_payload = _read_json_file(state_path)
    if not brainbit_config.get("enabled"):
        status = "disabled"
    elif state_payload:
        status = state_payload.get("status", "unknown")
    else:
        status = "waiting"

    return {
        **enabled_state,
        "status": status,
        "state_file": str(state_path),
        "latest": state_payload,
    }


def _enabled_status(config: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(config.get("enabled", False))}


def _merge_config_status(config: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    if not config:
        return {"enabled": False, "status": "planned", **status}
    return {**_enabled_status(config), **status}


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _platform_keys() -> tuple[str, ...]:
    if os.name == "nt":
        return ("windows", "win32", "default")
    if sys.platform == "darwin":
        return ("macos", "mac", "darwin", "default")
    return ("linux", "posix", "default")


def _resolve_platform_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    for key in _platform_keys():
        if key in value and value.get(key) not in (None, ""):
            return value.get(key)

    for key in ("default", "windows", "macos", "linux"):
        if key in value and value.get(key) not in (None, ""):
            return value.get(key)

    return None


def _resolve_project_path(base_dir: Path, value: str | None) -> Path:
    path = Path(value or "").expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
