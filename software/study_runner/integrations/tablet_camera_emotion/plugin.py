from __future__ import annotations

from typing import Any

from ..adapter_utils import config_section
from ..plugin_api import IntegrationContext, IntegrationPlugin


def _initialize(context: IntegrationContext) -> None:
    config = config_section(context, "camera_emotion", "camera")
    if not config:
        return

    from . import adapter

    lsl_config = config.get("lsl") or {}
    adapter.initialize(
        enabled=config.get("enabled", False),
        snapshot_interval_ms=config.get("snapshot_interval_ms", 200),
        store_raw_frames=config.get("store_raw_frames", False),
        overlay_enabled=config.get("overlay_enabled", True),
        worker_mode=config.get("worker_mode", "local_worker"),
        emotion_worker_url=config.get("emotion_worker_url", "http://127.0.0.1:3001"),
        emotion_worker_timeout_ms=config.get("emotion_worker_timeout_ms", 5000),
        auto_install=config.get("auto_install", True),
        lsl_enabled=lsl_config.get("enabled", False),
        lsl_auto_install=lsl_config.get("auto_install", True),
        lsl_stream_name=lsl_config.get("stream_name", "CameraEmotion"),
    )
    if config.get("enabled") and config.get("worker_mode", "local_worker") == "local_worker":
        from ..local_emotion_worker import plugin as emotion_worker_plugin

        emotion_worker_plugin.ensure_started(context)


def _status(context: IntegrationContext) -> dict[str, Any]:
    from . import adapter

    return {**adapter.get_status(), "device_label": "Tablet selfie camera"}


def _start(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
    result = adapter.start()
    config = config_section(context, "camera_emotion", "camera")
    if config.get("worker_mode", "local_worker") == "local_worker":
        from ..local_emotion_worker import plugin as emotion_worker_plugin

        emotion_worker_plugin.ensure_started(context)
    return result


def _stop(context: IntegrationContext) -> Any:
    from . import adapter
    from ..local_emotion_worker import plugin as emotion_worker_plugin

    result = adapter.stop()
    emotion_worker_plugin.stop_worker(context)
    return result


def _interval(context: IntegrationContext, start_epoch: float, end_epoch: float) -> dict[str, Any]:
    from . import adapter

    return adapter.get_interval_summary(start_epoch, end_epoch)


def _export(context: IntegrationContext, start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    from . import adapter

    return adapter.export_interval_samples(start_epoch, end_epoch)


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
    get_interval_summary=_interval,
    export_interval_samples=_export,
    sidecar_sensor="camera_emotion",
    sidecar_filename_suffix="camera_emotion_signals",
    sidecar_output_key="camera_emotion_file",
)
