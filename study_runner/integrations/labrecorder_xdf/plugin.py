from __future__ import annotations

from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = context.hardware_config.get("labrecorder", {})
    config = config if isinstance(config, dict) else {}
    enabled = bool(config.get("enabled", False))
    source_dir = context.resolve_platform_value(config.get("xdf_source_dir")) or ""
    return {
        "status": "enabled" if enabled else "disabled",
        "runtime_enabled": enabled,
        "xdf_source_dir": source_dir,
        "move_xdf": bool(config.get("move_xdf", False)),
        "device_label": "LabRecorder XDF collector",
    }


PLUGIN = IntegrationPlugin(
    key="labrecorder",
    label="LabRecorder / XDF",
    category="recording",
    config_key="labrecorder",
    has_recording=True,
    get_status=_status,
)
