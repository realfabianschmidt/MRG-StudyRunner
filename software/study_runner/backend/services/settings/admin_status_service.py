from __future__ import annotations

from typing import Any

from study_runner.plugin_framework.plugin_api import PluginContext
from study_runner.plugin_framework.registry import get_plugin_statuses
from ..studies.study_client_service import get_client_status


def build_admin_status(
    context: PluginContext,
    *,
    sensor_coordinator: Any = None,
    clock_sync_service: Any = None,
) -> dict[str, Any]:
    """Build the compact status payload consumed by the Admin Dashboard."""
    coordinator_status = sensor_coordinator.build_status(context) if sensor_coordinator else None
    return {
        "ok": True,
        "study_clients": get_client_status(),
        "plugins": coordinator_status["plugins"] if coordinator_status else get_plugin_statuses(context),
        "sensor_coordinator": _coordinator_public_payload(coordinator_status),
        "clock_sync": clock_sync_service.summary() if clock_sync_service else {"ok": True, "sources": {}},
        "timestamp_strategy": {
            "primary": "LSL",
            "recording_format": ".xdf",
            "note": "Use LSL timestamps as the primary synchronization layer and keep source timestamps as metadata.",
        },
    }


def _coordinator_public_payload(coordinator_status: dict[str, Any] | None) -> dict[str, Any]:
    """The coordinator's own diagnostics, without the per-plugin status it carries.

    The dashboard reads that from the top-level ``plugins`` key, so repeating it
    here would double the payload on every poll.
    """
    if not coordinator_status:
        return {"ok": True, "sample_metadata_model": []}
    return {key: value for key, value in coordinator_status.items() if key != "plugins"}
