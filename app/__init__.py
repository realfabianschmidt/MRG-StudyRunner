import json
import os
import sys
from pathlib import Path

from flask import Flask

from .routes import register_routes
from .secrets_service import load_local_secrets, resolve_notion_api_key


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


def _initialize_integrations(hardware_config: dict, local_secrets: dict) -> None:
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

    mini_radar_config = hardware_config.get("mini_radar") or hardware_config.get("radar") or {}
    if mini_radar_config:
        from .integrations import mini_radar_adapter

        mini_radar_lsl_config = mini_radar_config.get("lsl", {})
        mini_radar_adapter.initialize(
            enabled=mini_radar_config.get("enabled", False),
            port=_resolve_platform_value(mini_radar_config.get("port")) or "",
            baudrate=mini_radar_config.get("baudrate", 115200),
            auto_install=mini_radar_config.get("auto_install", True),
            auto_reconnect=mini_radar_config.get("auto_reconnect", True),
            reconnect_delay=mini_radar_config.get("reconnect_delay", 5),
            data_timeout_seconds=mini_radar_config.get("data_timeout_seconds", 5),
            lsl_enabled=mini_radar_lsl_config.get("enabled", False),
            lsl_auto_install=mini_radar_lsl_config.get("auto_install", True),
            lsl_stream_prefix=mini_radar_lsl_config.get("stream_prefix", "MiniRadar"),
            log_dir=_resolve_project_path(
                _resolve_platform_value(mini_radar_config.get("log_dir")) or "data"
            ),
        )

    camera_config = hardware_config.get("camera_emotion") or hardware_config.get("camera") or {}
    if camera_config:
        from .integrations import camera_affect_adapter

        camera_lsl_config = camera_config.get("lsl", {})
        camera_affect_adapter.initialize(
            enabled=camera_config.get("enabled", False),
            snapshot_interval_ms=camera_config.get("snapshot_interval_ms", 1000),
            store_raw_frames=camera_config.get("store_raw_frames", False),
            overlay_enabled=camera_config.get("overlay_enabled", True),
            worker_mode=camera_config.get("worker_mode", "mock"),
            emotion_worker_url=camera_config.get("emotion_worker_url", ""),
            emotion_worker_timeout_ms=camera_config.get("emotion_worker_timeout_ms", 5000),
            auto_install=camera_config.get("auto_install", True),
            lsl_enabled=camera_lsl_config.get("enabled", False),
            lsl_auto_install=camera_lsl_config.get("auto_install", True),
            lsl_stream_name=camera_lsl_config.get("stream_name", "CameraEmotion"),
        )

    notion_config = hardware_config.get("notion", {})
    if notion_config.get("enabled"):
        from .integrations import notion_adapter
        notion_adapter.initialize(
            enabled=True,
            api_key=resolve_notion_api_key(hardware_config, local_secrets),
            auto_retry_failed=notion_config.get("auto_retry_failed", True),
            timeout_seconds=notion_config.get("timeout_seconds", 10),
            data_dir=BASE_DIR / "data",
        )



def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
    app.config["BASE_DIR"] = BASE_DIR
    app.config["CONFIG_FILE"] = BASE_DIR / "study_config.json"
    app.config["DATA_DIR"] = BASE_DIR / "data"
    app.config["LOCAL_SECRETS_FILE"] = BASE_DIR / "local_secrets.json"
    app.config["ALLOW_UNSAFE_STIMULUS_CODE"] = (
        os.getenv("STUDY_RUNNER_ALLOW_UNSAFE_STIMULUS_CODE", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    app.config["DATA_DIR"].mkdir(exist_ok=True)

    hardware_config = _load_hardware_config()
    local_secrets = load_local_secrets(app.config["LOCAL_SECRETS_FILE"])
    app.config["HARDWARE_CONFIG"] = hardware_config
    app.config["LOCAL_SECRETS"] = local_secrets
    if os.getenv("STUDY_RUNNER_DISABLE_HARDWARE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        _initialize_integrations(hardware_config, local_secrets)

    register_routes(app)
    return app
