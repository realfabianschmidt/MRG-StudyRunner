import json
from pathlib import Path

from flask import Flask

from .routes import register_routes


BASE_DIR = Path(__file__).resolve().parent.parent


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
        )

    osc_config = hardware_config.get("osc", {})
    if osc_config.get("enabled"):
        from .integrations import osc_adapter
        osc_adapter.initialize(
            host=osc_config.get("host", "127.0.0.1"),
            port=osc_config.get("port", 9000),
            address_start=osc_config.get("address_start", "/study/start"),
            address_stop=osc_config.get("address_stop", "/study/stop"),
        )


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
    app.config["BASE_DIR"] = BASE_DIR
    app.config["CONFIG_FILE"] = BASE_DIR / "study_config.json"
    app.config["DATA_DIR"] = BASE_DIR / "data"
    app.config["DATA_DIR"].mkdir(exist_ok=True)

    hardware_config = _load_hardware_config()
    _initialize_integrations(hardware_config)

    register_routes(app)
    return app
