import json
import os
from pathlib import Path

from flask import Flask, request

from study_runner.plugin_framework.registry import build_context, initialize_plugins
from .routes import register_routes
from .services.runtime_config import (
    get_app_mode,
    get_project_base_dir,
    initialize_runtime_storage,
    is_background_disabled,
    read_server_host,
    read_server_port,
    resolve_runtime_paths,
)
from .services.clock_sync_service import ClockSyncService
from .services.finalization_runtime import configure_finalization
from .services.hardware_settings_service import (
    migrate_moved_plugin_paths,
    save_hardware_config,
)
from .services.recording_runtime import (
    RecordingRuntimeService,
    RuntimeRecordingFinalizationAdapter,
)
from .services.secrets_service import load_local_secrets
from .services.sensor_coordinator_service import SensorCoordinator
from .services.sensor_flush_service import SensorFlushService
from .services.study_client_service import reset_client_status
from .services.session_store import SessionStore
from .services.study_config_service import load_config
from .services.study_run_state_service import StudyRunStateStore
from .services.trial_event_service import TrialEventService
from .services.trial_service import stop_trial_session
from .services.upload_runtime import configure_upload_jobs


BASE_DIR = get_project_base_dir()
WEB_INTERFACE_DIR = BASE_DIR / "study_runner" / "web"


def _load_hardware_config(config_path: Path) -> dict:
    """Read hardware settings, repointing paths left over from the folder rename."""
    if not config_path.exists():
        return {}
    try:
        with config_path.open(encoding="utf-8") as file_handle:
            config = json.load(file_handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[HARDWARE] Could not read {config_path.name}: {error}")
        return {}

    config, moved = migrate_moved_plugin_paths(config)
    if moved:
        # Write it back so the repair happens once rather than on every start.
        try:
            save_hardware_config(config_path, config)
            print(f"[HARDWARE] Repointed {moved} plugin path(s) at the renamed plugins folder.")
        except OSError as error:
            print(f"[HARDWARE] Could not persist migrated plugin paths: {error}")
    return config


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
    app.config["BRANDING_DIR"] = runtime_paths.branding_dir
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
    app.config["SESSION_SENSOR_OVERRIDES"] = {}
    reset_client_status()
    app.config["CLOCK_SYNC_SERVICE"] = ClockSyncService()
    app.config["SENSOR_COORDINATOR"] = SensorCoordinator()
    app.config["SESSION_STORE"] = SessionStore(app.config["DATA_DIR"])
    app.config["TRIAL_EVENT_SERVICE"] = TrialEventService(
        app.config["DATA_DIR"],
        scheduling_enabled=not is_background_disabled(),
    )
    app.config["STUDY_RUN_STATE"] = StudyRunStateStore(app.config["DATA_DIR"])
    try:
        app.config["STUDY_RUN_STATE"].ensure_loaded(load_config(app.config["CONFIG_FILE"]).get("study_id", ""))
    except Exception as error:
        print(f"[STUDY-RUN] Could not initialize run state: {error}")
    app.config["SENSOR_FLUSH_SERVICE"] = SensorFlushService(app)

    hardware_disabled = _hardware_disabled()
    app.config["HARDWARE_DISABLED"] = hardware_disabled

    if not hardware_disabled:
        initialize_plugins(_integration_context(app))

    configure_upload_jobs(app)
    configured_worker = os.getenv("STUDY_RUNNER_XDF_WORKER", "").strip()
    recording_runtime = RecordingRuntimeService(
        app.config["DATA_DIR"],
        app.config["BASE_DIR"],
        configured_worker_path=Path(configured_worker) if configured_worker else None,
    )
    app.config["RECORDING_RUNTIME_SERVICE"] = recording_runtime
    app.config["FINALIZATION_RECORDING_ADAPTER"] = RuntimeRecordingFinalizationAdapter(
        recording_runtime,
        write_end_marker=lambda context: _write_finalization_end_marker(app, context),
        stop_producers=lambda context: _stop_finalization_producers(app, context),
    )
    configure_finalization(app)
    register_routes(app)
    _install_cache_policy(app)
    app.config["TRIAL_EVENT_SERVICE"].resume_pending(stop_trial_session)
    return app


def _install_cache_policy(app: Flask) -> None:
    """Never let a browser serve yesterday's interface.

    Study Runner updates in place: the operator restarts the server and the
    tablet just reloads. Without this, both keep whatever HTML and modules they
    cached, so a shipped change silently does not arrive - which is exactly how
    a new participant screen went missing on a tablet nobody thought to
    hard-reload.

    The pages are tiny and gate every module, so they are never stored. Static
    assets revalidate instead: Flask already sends ETag and Last-Modified, so an
    unchanged file answers 304 and stays fast on a lab LAN, while a changed one
    is always fetched.
    """

    @app.after_request
    def apply_cache_policy(response):
        # Files are served in passthrough mode; setting a header does not touch
        # the body, so they must not be skipped - they are the whole point.
        path = request.path or ""
        if path in {"/", "/admin"}:
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _write_finalization_end_marker(app: Flask, context) -> dict:
    """Write the terminal marker before producers and the worker are drained."""

    event = context.submission.get("study_end_event")
    event = event if isinstance(event, dict) else {}
    options = {
        "event_id": str(event.get("event_id") or f"study-end-{context.state['session_id']}"),
        "session_id": context.state["session_id"],
        "participant_id": context.state["participant_id"],
        "study_id": context.state["study_id"],
        "source_epoch_ms": event.get("source_epoch_ms"),
        "source_monotonic_ms": event.get("source_monotonic_ms"),
        "sequence_number": event.get("sequence_number"),
        "marker_event": "study_end",
        "phase": "study_end",
    }
    with app.app_context():
        from .services.trial_service import send_trial_marker

        return app.config["TRIAL_EVENT_SERVICE"].execute(
            options["event_id"],
            "study_end",
            options,
            lambda persisted: send_trial_marker("study_end", persisted),
        )


def _stop_finalization_producers(app: Flask, _context) -> dict:
    """Stop app-owned producers while retaining XDF inlets until worker freeze."""

    with app.app_context():
        from .routes.helpers import _stop_study_sensor_runtime

        return _stop_study_sensor_runtime()
