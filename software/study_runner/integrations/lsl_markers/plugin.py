from __future__ import annotations

from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


def _config_section(context: IntegrationContext) -> dict[str, Any]:
    section = context.hardware_config.get("lsl", {})
    return section if isinstance(section, dict) else {}


def _initialize(context: IntegrationContext) -> None:
    config = _config_section(context)
    if not config.get("enabled"):
        return
    from . import adapter

    adapter.initialize(
        stream_name=config.get("stream_name", "StudyRunner"),
        stream_type=config.get("stream_type", "Markers"),
        auto_install=config.get("auto_install", True),
    )


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = _config_section(context)
    enabled = bool(config.get("enabled", False))
    return {
        "status": "enabled" if enabled else "disabled",
        "runtime_enabled": enabled,
        "stream_name": config.get("stream_name", "StudyRunner"),
        "stream_type": config.get("stream_type", "Markers"),
        "device_label": "LSL marker stream",
    }


def _stop(context: IntegrationContext) -> None:
    from . import adapter

    adapter.stop()


def _trial_start(context: IntegrationContext, options: dict[str, Any]) -> None:
    _send_marker(options, "study:start")


def _trial_stop(context: IntegrationContext, options: dict[str, Any]) -> None:
    _send_marker(options, "study:stop")


def _trial_marker(context: IntegrationContext, options: dict[str, Any]) -> None:
    _send_marker(options, "study:marker")


def _send_marker(options: dict[str, Any], fallback: str) -> None:
    if not options.get("send_signal", True):
        return
    from . import adapter

    adapter.send_marker(str(options.get("marker_value") or fallback))


PLUGIN = IntegrationPlugin(
    key="lsl",
    label="LSL markers",
    category="sync",
    config_key="lsl",
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
    stop=_stop,
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
    on_trial_marker=_trial_marker,
)
