from __future__ import annotations

from typing import Any

from plugins.plugin_api import IntegrationContext
from plugins.registry import get_integration_statuses
from .study_client_service import get_client_status


def build_admin_status(context: IntegrationContext) -> dict[str, Any]:
    """Build the compact status payload consumed by the Admin Dashboard."""
    return {
        "ok": True,
        "study_clients": get_client_status(),
        "integrations": get_integration_statuses(context),
        "timestamp_strategy": {
            "primary": "LSL",
            "recording_format": ".xdf",
            "note": "Use LSL timestamps as the primary synchronization layer and keep source timestamps as metadata.",
        },
    }
