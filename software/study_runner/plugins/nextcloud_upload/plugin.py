"""Catalog adapter for the existing Nextcloud destination service.

Upload execution remains in the backend destination service.  This small
plugin only gives it the same capability/status contract as other built-ins.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from study_runner.plugin_framework.plugin_api import IntegrationContext, IntegrationPlugin


def _status(context: IntegrationContext) -> dict[str, Any]:
    section = context.hardware_config.get("nextcloud")
    configured = section if isinstance(section, dict) else {}
    return {
        "status": "available",
        "runtime_enabled": True,
        "configured_enabled": bool(configured.get("enabled", True)),
        "device_label": "Nextcloud upload",
        "last_message": "Nextcloud is available as a per-study upload destination.",
    }


def _publish(context: IntegrationContext, payload: dict[str, Any]) -> dict[str, Any]:
    from study_runner.backend.services.nextcloud_service import NextcloudPublicShareClient
    from study_runner.backend.services.secrets_service import resolve_nextcloud_password

    config_data = payload.get("config_data") or {}
    study_settings = config_data.get("study_settings") or {}
    plugins = study_settings.get("plugins")
    selection = plugins.get("nextcloud") if isinstance(plugins, dict) else None
    if isinstance(selection, dict):
        enabled = bool(selection.get("enabled"))
        plugin_settings = selection.get("settings")
        plugin_settings = plugin_settings if isinstance(plugin_settings, dict) else {}
        share_link = str(plugin_settings.get("share_link") or "").strip()
    else:
        # Jobs committed before API v3 remain replayable.
        enabled = bool(study_settings.get("nextcloud_enabled"))
        share_link = str(study_settings.get("nextcloud_share_link") or "").strip()
    if not enabled or not share_link:
        return {
            "ok": False,
            "skipped": True,
            "error": "Nextcloud is no longer configured for this study.",
        }

    study_id = str(
        config_data.get("study_id")
        or (payload.get("result_payload") or {}).get("study_id")
        or ""
    )
    nextcloud_config = context.hardware_config.get("nextcloud") or {}
    password = resolve_nextcloud_password(
        context.hardware_config,
        context.local_secrets,
        study_id,
    )
    timeout_seconds = int(nextcloud_config.get("timeout_seconds") or 30)

    saved_output = payload.get("saved_output") or {}
    relative_folder = str(
        saved_output.get("session_relative_path")
        or saved_output.get("session_dir")
        or saved_output.get("participant_dir")
        or ""
    )
    root = Path(context.data_dir).resolve()
    local_folder = (root / relative_folder).resolve()
    legacy_folder = (root.parent / relative_folder).resolve()
    if relative_folder and not local_folder.is_dir() and legacy_folder.is_relative_to(root):
        local_folder = legacy_folder
    if not relative_folder or not local_folder.is_relative_to(root):
        return {
            "ok": False,
            "error": "The saved session folder is outside the results directory.",
        }

    result_payload = payload.get("result_payload") or {}
    return NextcloudPublicShareClient(
        share_link,
        password=password,
        timeout_seconds=timeout_seconds,
    ).upload_session_folder(
        local_folder,
        study_id=str(result_payload.get("study_id") or ""),
        participant_id=str(result_payload.get("participant_id") or ""),
        session_relative_path=str(saved_output.get("session_relative_path") or ""),
    )


PLUGIN = IntegrationPlugin(
    key="nextcloud",
    label="Nextcloud upload",
    category="storage",
    config_key="nextcloud",
    can_toggle=False,
    get_status=_status,
    publish_destination=_publish,
)
