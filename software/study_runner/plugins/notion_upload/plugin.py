from __future__ import annotations

from copy import deepcopy
from typing import Any

from study_runner.plugin_framework.adapter_utils import config_section
from study_runner.plugin_framework.plugin_api import PluginContext, Plugin


def _initialize(context: PluginContext) -> None:
    config = config_section(context, "notion")
    from . import adapter

    adapter.initialize(
        enabled=bool(config.get("enabled")),
        api_key=context.secret("notion"),
        auto_retry_failed=config.get("auto_retry_failed", True),
        timeout_seconds=config.get("timeout_seconds", 10),
        data_dir=context.data_dir,
    )


def _status(context: PluginContext) -> dict[str, Any]:
    config = config_section(context, "notion")
    from . import adapter

    status = adapter.get_status()
    has_key = bool(context.secret("notion"))
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


def _publish(context: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one queued publication through the plugin boundary.

    The existing adapter still consumes its historic flat field names. They
    are projected only into this private attempt copy; persisted study files
    remain canonical API-v3 plugin selections.
    """

    from . import adapter

    config_data = deepcopy(payload.get("config_data") or {})
    study_settings = config_data.setdefault("study_settings", {})
    plugins = study_settings.get("plugins")
    selection = plugins.get("notion") if isinstance(plugins, dict) else None
    if isinstance(selection, dict):
        plugin_settings = selection.get("settings")
        plugin_settings = plugin_settings if isinstance(plugin_settings, dict) else {}
        study_settings.update(
            {
                "notion_enabled": bool(selection.get("enabled")),
                "notion_parent_page_id": str(plugin_settings.get("parent_page_id") or "").strip(),
                "notion_database_id": str(plugin_settings.get("database_id") or "").strip(),
                "notion_data_source_id": str(plugin_settings.get("data_source_id") or "").strip(),
            }
        )

    return adapter.upload_study_result(
        result_payload=payload.get("result_payload") or {},
        hardware_config=payload.get("hardware_config") or context.hardware_config,
        saved_output=payload.get("saved_output") or {},
        config_data=config_data,
        # Finalization already committed an immutable per-session config
        # snapshot. The adapter's legacy retry refresh expects flat fields and
        # would discard this private v3 projection.
        is_retry=False,
    )


PLUGIN = Plugin(
    key="notion",
    label="Notion upload",
    category="storage",
    config_key="notion",
    initialize=_initialize,
    get_status=_status,
    publish_destination=_publish,
)
