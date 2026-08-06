from __future__ import annotations

from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section
from study_runner.plugin_framework.plugin_api import IntegrationContext, IntegrationPlugin


def _initialize(context: IntegrationContext) -> None:
    config = config_section(context, "camera_emotion", "camera")
    if not config:
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
    adapter.initialize(
        enabled=config.get("enabled", False),
        snapshot_interval_ms=config.get("snapshot_interval_ms", 1000),
        store_raw_frames=config.get("store_raw_frames", False),
        overlay_enabled=config.get("overlay_enabled", True),
        worker_mode=config.get("worker_mode", "local_worker"),
        emotion_worker_url=config.get("emotion_worker_url", "http://127.0.0.1:3001"),
        emotion_worker_timeout_ms=config.get("emotion_worker_timeout_ms", 5000),
        auto_install=config.get("auto_install", True),
        lsl_enabled=bool(config.get("enabled", False)),
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_name=lsl_config.get("stream_name", "CameraEmotion"),
    )
    if config.get("enabled") and config.get("worker_mode", "local_worker") == "local_worker":
        from .worker import plugin as emotion_worker_plugin

        emotion_worker_plugin.ensure_started(context)


def _status(context: IntegrationContext) -> dict[str, Any]:
    from . import adapter

    status = {
        **adapter.get_status(),
        "device_label": "Tablet selfie camera",
        "preview": adapter.get_preview_status(),
    }
    try:
        from .worker import plugin as emotion_worker_plugin

        handler = emotion_worker_plugin.PLUGIN.get_status
        status["emotion_worker"] = handler(context) if handler else {}
    except Exception as error:
        status["emotion_worker"] = {
            "status": "failed",
            "last_message": f"Emotion worker status failed: {error}",
        }
    return status


def _start(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
    result = adapter.start()
    config = config_section(context, "camera_emotion", "camera")
    if config.get("worker_mode", "local_worker") == "local_worker":
        from .worker import plugin as emotion_worker_plugin

        emotion_worker_plugin.ensure_started(context)
    return result


def _stop(context: IntegrationContext) -> Any:
    from . import adapter
    from .worker import plugin as emotion_worker_plugin

    result = adapter.stop()
    emotion_worker_plugin.stop_worker(context)
    return result


def _interval(context: IntegrationContext, start_epoch: float, end_epoch: float) -> dict[str, Any]:
    from . import adapter

    return adapter.get_interval_summary(start_epoch, end_epoch)


def _export(context: IntegrationContext, start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    from . import adapter

    return adapter.export_interval_samples(start_epoch, end_epoch)


def _run_participant_action(
    context: IntegrationContext,
    action_key: str,
    _payload: dict[str, Any],
) -> Any:
    """Own camera-monitor lifecycle behind the generic participant boundary."""

    from . import adapter

    if action_key == "start_monitor":
        # Participant monitoring may begin before the recording session has
        # initialized sensors. Apply the current study-effective config every
        # time so state from a previously loaded study cannot leak through.
        _initialize(context)
        runtime = _start(context)
        active = bool(runtime.get("enabled", False)) if isinstance(runtime, dict) else True
        preview = adapter.set_preview_active(active)
        return {"monitor_active": active, "preview": preview, "runtime": runtime}
    if action_key == "stop_monitor":
        return {"monitor_active": False, "preview": adapter.set_preview_active(False)}
    raise ValueError(f"Unknown camera emotion participant action: {action_key}")


def _ingest_participant(
    context: IntegrationContext,
    ingest_key: str,
    payload: dict[str, Any],
) -> Any:
    """Own browser-frame ingestion; generic HTTP code never imports the adapter."""

    if ingest_key != "frame":
        raise ValueError(f"Unknown camera emotion participant ingest: {ingest_key}")
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
    return adapter.process_frame(payload)


def _run_admin_action(
    context: IntegrationContext,
    action_key: str,
    _payload: dict[str, Any],
) -> Any:
    from .worker import plugin as emotion_worker_plugin

    handlers = {
        "repair_runtime": emotion_worker_plugin.repair_runtime,
        "install_dependencies": emotion_worker_plugin.install_dependencies,
    }
    try:
        handler = handlers[action_key]
    except KeyError as error:
        raise ValueError(f"Unknown camera emotion admin action: {action_key}") from error
    return handler(context)


PLUGIN = IntegrationPlugin(
    key="camera_emotion",
    label="Tablet camera emotion",
    category="biosignal",
    config_key="camera_emotion",
    can_start=True,
    can_stop=True,
    has_lsl=True,
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
    start=_start,
    stop=_stop,
    run_admin_action=_run_admin_action,
    run_participant_action=_run_participant_action,
    ingest_participant=_ingest_participant,
    get_interval_summary=_interval,
    export_interval_samples=_export,
    sidecar_sensor="camera_emotion",
    sidecar_filename_suffix="camera_emotion_signals",
    sidecar_output_key="camera_emotion_file",
)
