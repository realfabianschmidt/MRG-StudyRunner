from __future__ import annotations

from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


def _config_section(context: IntegrationContext) -> dict[str, Any]:
    section = context.hardware_config.get("notion", {})
    return section if isinstance(section, dict) else {}


def _initialize(context: IntegrationContext) -> None:
    config = _config_section(context)
    from . import adapter

    adapter.initialize(
        enabled=bool(config.get("enabled")),
        api_key=context.notion_api_key(),
        auto_retry_failed=config.get("auto_retry_failed", True),
        timeout_seconds=config.get("timeout_seconds", 10),
        data_dir=context.data_dir,
    )


def _status(context: IntegrationContext) -> dict[str, Any]:
    config = _config_section(context)
    from . import adapter

    status = adapter.get_status()
    has_key = bool(context.notion_api_key())
    enabled = bool(config.get("enabled", False))
    if not enabled:
        status_value = "disabled"
    elif status.get("connected"):
        status_value = "connected"
    elif has_key:
        status_value = "waiting"
    else:
        status_value = "missing_key"

    return {
        **status,
        "status": status_value,
        "runtime_enabled": bool(status.get("connected")),
        "api_key_configured": has_key,
        "auto_retry_failed": bool(config.get("auto_retry_failed", True)),
        "device_label": "Notion upload",
        "last_message": _message(enabled, has_key, bool(status.get("connected"))),
    }


def _message(enabled: bool, has_key: bool, connected: bool) -> str:
    if not enabled:
        return "Notion upload is disabled."
    if not has_key:
        return "Notion upload is enabled but no backend-local API key is stored."
    if not connected:
        return "Notion API key is configured; client is not connected yet."
    return "Notion client is ready."


PLUGIN = IntegrationPlugin(
    key="notion",
    label="Notion upload",
    category="storage",
    config_key="notion",
    initialize=_initialize,
    get_status=_status,
)
