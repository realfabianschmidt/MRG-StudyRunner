from __future__ import annotations

from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


def _config_section(context: IntegrationContext) -> dict[str, Any]:
    config = context.hardware_config.get("camera_emotion") or context.hardware_config.get("camera") or {}
    return config if isinstance(config, dict) else {}


def _initialize(context: IntegrationContext) -> None:
    config = _config_section(context)
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


def _status(context: IntegrationContext) -> dict[str, Any]:
    from . import adapter

    return {**adapter.get_status(), "device_label": "Tablet selfie camera"}


def _start(context: IntegrationContext) -> Any:
    from . import adapter

    if not adapter.is_configured():
        _initialize(context)
    return adapter.start()


def _stop(context: IntegrationContext) -> Any:
    from . import adapter

    return adapter.stop()


def _interval(context: IntegrationContext, start_epoch: float, end_epoch: float) -> dict[str, Any]:
    from . import adapter

    return adapter.get_interval_summary(start_epoch, end_epoch)


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
)
