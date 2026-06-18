import json
import os
from pathlib import Path

from flask import Flask

from study_runner.integrations.registry import build_context, initialize_plugins
from .routes import register_routes
from .services.runtime_config import (
    get_app_mode,
    get_project_base_dir,
    initialize_runtime_storage,
    read_server_host,
    read_server_port,
    resolve_runtime_paths,
)
from .services.secrets_service import load_local_secrets


BASE_DIR = get_project_base_dir()
WEB_INTERFACE_DIR = BASE_DIR / "study_runner" / "web"


def _load_hardware_config(config_path: Path) -> dict:
    """Read hardware integration settings. Returns an empty dict if not found."""
    if not config_path.exists():
        return {}
    try:
        with config_path.open(encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[HARDWARE] Could not read {config_path.name}: {error}")
        return {}


def _hardware_disabled() -> bool:
    return os.getenv("STUDY_RUNNER_DISABLE_HARDWARE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _integration_context(app: Flask):
    return build_context(
        base_dir=app.config["BASE_DIR"],
        data_dir=app.config["DATA_DIR"],
        hardware_config=app.config.get("HARDWARE_CONFIG", {}),
        local_secrets=app.config.get("LOCAL_SECRETS", {}),
        local_secrets_file=app.config["LOCAL_SECRETS_FILE"],
    )


def create_app() -> Flask:
    runtime_paths = resolve_runtime_paths(BASE_DIR)
    initialize_runtime_storage(runtime_paths)

    app = Flask(
        __name__,
        static_folder=str(WEB_INTERFACE_DIR),
        static_url_path="/static",
    )

    app.config["BASE_DIR"] = runtime_paths.base_dir
    app.config["CONTENT_DIR"] = runtime_paths.content_dir
    app.config["STORAGE_ROOT"] = runtime_paths.storage_root
    app.config["SETTINGS_DIR"] = runtime_paths.settings_dir
    app.config["CONFIG_FILE"] = runtime_paths.config_file
    app.config["HARDWARE_CONFIG_FILE"] = runtime_paths.hardware_config_file
    app.config["DATA_DIR"] = runtime_paths.data_dir
    app.config["SAVED_STUDIES_DIR"] = runtime_paths.saved_studies_dir
    app.config["LOCAL_SECRETS_FILE"] = runtime_paths.local_secrets_file
    app.config["USES_EXTERNAL_STORAGE"] = runtime_paths.uses_external_storage
    app.config["APP_MODE"] = get_app_mode()
    app.config["SERVER_HOST"] = read_server_host()
    app.config["SERVER_PORT"] = read_server_port()
    app.config["ALLOW_UNSAFE_STIMULUS_CODE"] = (
        os.getenv("STUDY_RUNNER_ALLOW_UNSAFE_STIMULUS_CODE", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    hardware_config = _load_hardware_config(app.config["HARDWARE_CONFIG_FILE"])
    local_secrets = load_local_secrets(app.config["LOCAL_SECRETS_FILE"])
    app.config["HARDWARE_CONFIG"] = hardware_config
    app.config["LOCAL_SECRETS"] = local_secrets

    if not _hardware_disabled():
        initialize_plugins(_integration_context(app))

    register_routes(app)
    return app
