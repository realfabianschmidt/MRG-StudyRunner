from __future__ import annotations

from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section
from study_runner.plugin_framework.plugin_api import IntegrationContext, IntegrationPlugin


def _initialize(context: IntegrationContext) -> None:
    config = config_section(context, "lsl")
    from . import adapter

    adapter.initialize(
        stream_name=config.get("stream_name", "StudyRunner"),
        stream_type=config.get("stream_type", "Markers"),
        auto_install=config.get("auto_install", True),
    )


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = config_section(context, "lsl")
    return {
        "status": "enabled",
        "configured_enabled": True,
        "runtime_enabled": True,
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
    from . import adapter

    adapter.send_marker(str(options.get("marker_value") or fallback))


PLUGIN = IntegrationPlugin(
    key="lsl",
    label="LSL markers",
    category="sync",
    config_key="lsl",
    # Marker recording is mandatory infrastructure.  The manifest keeps it
    # out of user-facing settings and this backend guard also prevents a
    # crafted generic toggle request from disabling the canonical stream.
    can_toggle=False,
    has_recording=True,
    initialize=_initialize,
    get_status=_status,
    stop=_stop,
    on_trial_start=_trial_start,
    on_trial_stop=_trial_stop,
    on_trial_marker=_trial_marker,
)
