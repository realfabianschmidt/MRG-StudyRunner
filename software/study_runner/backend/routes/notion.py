"""Deprecated Notion-only aliases kept for one compatibility release.

New clients use the generic plugin catalog/actions and upload-job endpoints.
These shims deliberately never import the plugin package; removing the bundle
therefore yields HTTP 410 instead of crashing the core application.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from study_runner.plugin_framework.registry import get_plugin, get_plugin_status

from ..services.studies.study_config_service import load_config
from ..services.studies.study_plugin_config import normalize_study_settings_plugins
from ..services.studies.study_secrets_service import describe_secret_state
from ..services.studies.validation import validate_and_normalize_config
from .helpers import _plugin_context


bp = Blueprint("notion", __name__)
_PLUGIN_KEY = "notion"


def _deprecated(response, successor: str):
    flask_response = response[0] if isinstance(response, tuple) else response
    flask_response.headers["Deprecation"] = "true"
    flask_response.headers["Warning"] = (
        f'299 Study-Runner "Deprecated compatibility route; use {successor}"'
    )
    flask_response.headers["Link"] = f'<{successor}>; rel="successor-version"'
    return response


def _gone(successor: str):
    return _deprecated(
        (
            jsonify({
                "ok": False,
                "error": "The Notion plugin is not installed; this compatibility route is unavailable.",
            }),
            410,
        ),
        successor,
    )


@bp.route("/api/notion/status")
def notion_status():
    successor = "/api/admin/status"
    if get_plugin(_PLUGIN_KEY) is None:
        return _gone(successor)

    hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
    local_secrets = current_app.config.get("LOCAL_SECRETS", {})
    config_data = validate_and_normalize_config(
        load_config(current_app.config["CONFIG_FILE"])
    )
    study_settings = normalize_study_settings_plugins(
        config_data.get("study_settings") or {}
    )
    study_id = str(config_data.get("study_id") or "")
    selection = (study_settings.get("plugins") or {}).get(_PLUGIN_KEY) or {}
    destination_settings = selection.get("settings") or {}
    status = get_plugin_status(_PLUGIN_KEY, _plugin_context())
    upload_counts = current_app.config["UPLOAD_JOBS_SERVICE"].counts(kind=_PLUGIN_KEY)
    secret_state = describe_secret_state(
        _PLUGIN_KEY, hardware_config, local_secrets, study_id
    )
    status.update(
        {
            "queue_size": upload_counts["queued"] + upload_counts["running"],
            "failed_uploads": upload_counts["failed"],
            "enabled_globally": bool(hardware_config.get(_PLUGIN_KEY, {}).get("enabled")),
            "auto_retry_failed": bool(
                hardware_config.get(_PLUGIN_KEY, {}).get("auto_retry_failed", True)
            ),
            "api_key_configured": bool(secret_state["configured"]),
            "api_key_source": secret_state["source"],
            "current_study_id": study_id,
            "current_study_notion_enabled": bool(selection.get("enabled")),
            "current_study_parent_page_id": destination_settings.get("parent_page_id", ""),
            "current_study_database_id": destination_settings.get("database_id", ""),
            "current_study_target_ready": bool(
                destination_settings.get("parent_page_id")
                or destination_settings.get("database_id")
            ),
        }
    )
    return _deprecated(jsonify(status), successor)


@bp.route("/api/notion/flush-queue", methods=["POST"])
def notion_flush_queue():
    successor = "/api/uploads/retry"
    if get_plugin(_PLUGIN_KEY) is None:
        return _gone(successor)
    service = current_app.config["UPLOAD_JOBS_SERVICE"]
    return _deprecated(
        jsonify(service.retry(all_failed=True, kind=_PLUGIN_KEY)), successor
    )
