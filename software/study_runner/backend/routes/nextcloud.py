"""Nextcloud public-share connection test endpoint."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..services.delivery.nextcloud_service import test_connection
from ..services.studies.study_secrets_service import resolve_plugin_secret
from ..services.studies.study_config_service import load_config
from ..services.studies.validation import validate_and_normalize_config


bp = Blueprint("nextcloud", __name__)


@bp.route("/api/nextcloud/test", methods=["POST"])
def nextcloud_test():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Payload must be a JSON object."}), 400

    config_data = validate_and_normalize_config(
        load_config(current_app.config["CONFIG_FILE"])
    )
    study_settings = config_data.get("study_settings", {})
    share_link = str(
        payload.get("share_link")
        or study_settings.get("nextcloud_share_link")
        or ""
    ).strip()
    password = (
        str(payload.get("password") or "")
        if "password" in payload
        else resolve_plugin_secret(
            "nextcloud",
            current_app.config.get("HARDWARE_CONFIG", {}),
            current_app.config.get("LOCAL_SECRETS", {}),
            str(config_data.get("study_id") or ""),
        )
    )
    try:
        timeout_seconds = int(payload.get("timeout_seconds") or 10)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "timeout_seconds must be an integer."}), 400

    result = test_connection(
        share_link,
        password=password,
        timeout_seconds=timeout_seconds,
    )
    return jsonify(result)
