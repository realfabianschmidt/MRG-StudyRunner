import json
import os
import sys
from pathlib import Path

from flask import Flask

from .routes import register_routes


BASE_DIR = Path(__file__).resolve().parent.parent


def _platform_keys() -> tuple[str, ...]:
    if os.name == "nt":
        return ("windows", "win32", "default")
    if sys.platform == "darwin":
        return ("macos", "mac", "darwin", "default")
    return ("linux", "posix", "default")


def _resolve_platform_value(value):
    """Resolve a config value that may contain OS-specific overrides."""
    if not isinstance(value, dict):
        return value

    for key in _platform_keys():
        if key in value:
            selected = value.get(key)
            if selected not in (None, ""):
                return selected

    for fallback_key in ("default", "windows", "macos", "linux"):
        if fallback_key in value:
            selected = value.get(fallback_key)
            if selected not in (None, ""):
                return selected

    return None


def _default_brainbit_value(kind: str):
    defaults = {
        "script_path": "brainbit/brainbit_realtime_cli_OSC_15.py",
        "working_dir": "brainbit",
        "log_dir": "brainbit/logs",
    }
    return defaults[kind]


def _resolve_project_path(value: str | None) -> str | None:
    if not value:
        return None

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path.resolve())


def _load_hardware_config() -> dict:
    """Read hardware_config.json from the project root. Returns an empty dict if not found."""
    config_path = BASE_DIR / "hardware_config.json"
    if not config_path.exists():
        return {}
    try:
        with config_path.open(encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[HARDWARE] Could not read hardware_config.json: {error}")
        return {}


def _initialize_integrations(hardware_config: dict) -> None:
    """Start each hardware integration that is enabled in hardware_config.json."""
    lsl_config = hardware_config.get("lsl", {})
    if lsl_config.get("enabled"):
        from .integrations import lsl_adapter
        lsl_adapter.initialize(
            stream_name=lsl_config.get("stream_name", "StudyRunner"),
            stream_type=lsl_config.get("stream_type", "Markers"),
            auto_install=lsl_config.get("auto_install", True),
        )

    osc_config = hardware_config.get("osc", {})
    if osc_config.get("enabled"):
        from .integrations import osc_adapter
        osc_adapter.initialize(
            host=osc_config.get("host", "127.0.0.1"),
            port=osc_config.get("port", 9000),
            address_start=osc_config.get("address_start", "/study/start"),
            address_stop=osc_config.get("address_stop", "/study/stop"),
            auto_install=osc_config.get("auto_install", True),
        )

    brainbit_config = hardware_config.get("brainbit", {})
    if brainbit_config.get("enabled"):
        from .integrations import brainbit_adapter

        brainbit_lsl_config = brainbit_config.get("lsl", {})
        brainbit_adapter.initialize(
            script_path=_resolve_project_path(
                _resolve_platform_value(brainbit_config.get("script_path"))
                or _default_brainbit_value("script_path")
            ),
            working_dir=_resolve_project_path(
                _resolve_platform_value(brainbit_config.get("working_dir"))
                or _default_brainbit_value("working_dir")
            ),
            python_executable=_resolve_project_path(
                _resolve_platform_value(brainbit_config.get("python_executable"))
            ),
            osc_host=brainbit_config.get("osc_host", "127.0.0.1"),
            osc_port=brainbit_config.get("osc_port", 8000),
            scan_seconds=brainbit_config.get("scan_seconds", 5),
            resist_seconds=brainbit_config.get("resist_seconds", 6),
            signal_seconds=brainbit_config.get("signal_seconds", 0),
            pretty=brainbit_config.get("pretty", True),
            debug=brainbit_config.get("debug", False),
            lsl_enabled=brainbit_lsl_config.get("enabled", False),
            lsl_auto_install=brainbit_lsl_config.get("auto_install", True),
            lsl_stream_prefix=brainbit_lsl_config.get("stream_prefix", "BrainBit"),
            quiet_output=brainbit_config.get("quiet_output", True),
            open_monitor_terminal=brainbit_config.get("open_monitor_terminal", True),
            monitor_refresh_ms=brainbit_config.get("monitor_refresh_ms", 1000),
            disconnect_timeout_ms=brainbit_config.get("disconnect_timeout_ms", 5000),
            log_dir=_resolve_project_path(
                _resolve_platform_value(brainbit_config.get("log_dir"))
                or _default_brainbit_value("log_dir")
            ),
        )


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
    app.config["BASE_DIR"] = BASE_DIR
    app.config["CONFIG_FILE"] = BASE_DIR / "study_config.json"
    app.config["DATA_DIR"] = BASE_DIR / "data"
    app.config["DATA_DIR"].mkdir(exist_ok=True)

    hardware_config = _load_hardware_config()
    app.config["HARDWARE_CONFIG"] = hardware_config
    if os.getenv("STUDY_RUNNER_DISABLE_HARDWARE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        _initialize_integrations(hardware_config)

    register_routes(app)
    return app
