"""Notion upload integration endpoints (status, queue flush, connection test)."""
from flask import Blueprint, current_app, jsonify, request

from ..services.settings.secrets_service import (
    describe_notion_api_key_source,
    describe_notion_api_key_storage,
    resolve_notion_api_key,
)
from ..services.studies.study_config_service import load_config
from ..services.shared.validation import validate_and_normalize_config

bp = Blueprint("notion", __name__)


def _loaded_study_id() -> str:
    """The study whose credentials apply right now."""
    try:
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        return str(config_data.get("study_id") or "")
    except Exception:
        return ""


@bp.route("/api/notion/status")
def notion_status():
    from study_runner.plugins.notion_upload import adapter as notion_adapter

    hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
    local_secrets = current_app.config.get("LOCAL_SECRETS", {})
    config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
    study_settings = config_data.get("study_settings", {})
    study_id = str(config_data.get("study_id") or "")

    status = notion_adapter.get_status()
    upload_counts = current_app.config["UPLOAD_JOBS_SERVICE"].counts(kind="notion")
    status.update(
        {
            "queue_size": upload_counts["queued"] + upload_counts["running"],
            "failed_uploads": upload_counts["failed"],
            "enabled_globally": bool(hardware_config.get("notion", {}).get("enabled")),
            "auto_retry_failed": bool(hardware_config.get("notion", {}).get("auto_retry_failed", True)),
            "api_key_configured": bool(resolve_notion_api_key(hardware_config, local_secrets, study_id)),
            "api_key_source": describe_notion_api_key_source(hardware_config, local_secrets, study_id),
            "api_key_storage": describe_notion_api_key_storage(hardware_config, local_secrets, current_app.config["LOCAL_SECRETS_FILE"], study_id),
            "local_secrets_file": current_app.config["LOCAL_SECRETS_FILE"].name,
            "current_study_id": config_data.get("study_id", ""),
            "current_study_notion_enabled": bool(study_settings.get("notion_enabled")),
            "current_study_parent_page_id": study_settings.get("notion_parent_page_id", ""),
            "current_study_database_id": study_settings.get("notion_database_id", ""),
            "current_study_target_ready": bool(study_settings.get("notion_parent_page_id") or study_settings.get("notion_database_id")),
        }
    )
    return jsonify(status)


@bp.route("/api/notion/flush-queue", methods=["POST"])
def notion_flush_queue():
    service = current_app.config["UPLOAD_JOBS_SERVICE"]
    return jsonify(service.retry(all_failed=True, kind="notion"))


@bp.route("/api/notion/test", methods=["POST"])
def notion_test():
    from study_runner.plugins.notion_upload import adapter as notion_adapter

    payload = request.get_json() or {}
    result = notion_adapter.test_connection(
        api_key=(
            str(payload.get("api_key") or "").strip()
            or resolve_notion_api_key(
                current_app.config.get("HARDWARE_CONFIG", {}),
                current_app.config.get("LOCAL_SECRETS", {}),
                _loaded_study_id(),
            )
        ),
        timeout_seconds=int(payload.get("timeout_seconds") or 10),
    )
    return jsonify(result)
