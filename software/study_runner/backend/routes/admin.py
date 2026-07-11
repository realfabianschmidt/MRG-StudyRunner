"""Operator/admin endpoints: health, studies, status, restart, shortcut."""
import sys
import threading

from flask import Blueprint, current_app, jsonify, request

from study_runner.integrations.registry import initialize_plugin, run_runtime_action
from ..services.admin_status_service import build_admin_status
from ..services.runtime_config import build_runtime_info
from ..services.shortcut_service import ShortcutError, create_desktop_shortcut
from ..services.study_config_service import delete_study, list_studies, load_study, save_config
from ..services.study_sensor_runtime import STUDY_SENSOR_KEYS
from ..services.validation import validate_and_normalize_config
from .helpers import (
    _clear_session_overrides,
    _delayed_shutdown,
    _integration_context,
    _rebuild_active_study_runtime_config,
    _sensor_runtime_state,
    _session_overrides,
    _spawn_server_restart,
)

bp = Blueprint("admin", __name__)


@bp.route("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "status": "running",
            "app_mode": current_app.config.get("APP_MODE", "python"),
        }
    )


@bp.route("/api/runtime-info")
def runtime_info():
    return jsonify(build_runtime_info(current_app.config))


@bp.route("/api/admin/restart", methods=["POST"])
def admin_restart():
    app_mode = str(current_app.config.get("APP_MODE", "python")).strip().lower()
    if app_mode in {"desktop", "packaged"} or getattr(sys, "frozen", False):
        return jsonify(
            {
                "ok": False,
                "error": "Server restart is unavailable in packaged builds. Close and reopen Study Runner, or use the update restart action after staging an update.",
            }
        ), 503

    shutdown_func = request.environ.get("werkzeug.server.shutdown")
    if shutdown_func is None:
        return jsonify({"ok": False, "error": "Server restart is only available on the built-in Study Runner server."}), 503

    try:
        _spawn_server_restart(current_app.config["BASE_DIR"])
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    threading.Thread(target=_delayed_shutdown, args=(shutdown_func,), daemon=True).start()
    return jsonify({"ok": True, "message": "Server restart requested."})


@bp.route("/api/admin/studies", methods=["GET"])
def admin_list_studies():
    return jsonify(list_studies(current_app.config["SAVED_STUDIES_DIR"]))


@bp.route("/api/admin/studies/active", methods=["POST"])
def admin_set_active_study():
    payload = request.get_json() or {}
    study_id = payload.get("id")
    if not study_id:
        return jsonify({"ok": False, "error": "No study ID provided"}), 400
    try:
        config_data = load_study(current_app.config["SAVED_STUDIES_DIR"], study_id)
        validated_config = validate_and_normalize_config(config_data)
        save_config(current_app.config["CONFIG_FILE"], validated_config)
        print(f"[CONFIG] Activated study: {study_id}")
        return jsonify(validated_config)
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/studies/<study_id>", methods=["GET"])
def admin_get_study(study_id):
    try:
        return jsonify(load_study(current_app.config["SAVED_STUDIES_DIR"], study_id))
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/studies/<study_id>", methods=["DELETE"])
def admin_delete_study(study_id):
    if delete_study(current_app.config["SAVED_STUDIES_DIR"], study_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Not found"}), 404


@bp.route("/api/admin/status")
def admin_status():
    payload = build_admin_status(_integration_context())
    payload["active_study_session"] = bool(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
    payload["study_controlled_sensor_keys"] = list(STUDY_SENSOR_KEYS)
    payload["sensor_runtime"] = _sensor_runtime_state()
    payload["session_sensor_overrides"] = _session_overrides()
    return jsonify(payload)


@bp.route("/api/admin/session-overrides/reset", methods=["POST"])
def reset_session_overrides():
    _clear_session_overrides()
    active_config = _rebuild_active_study_runtime_config()
    context = _integration_context(active_config) if isinstance(active_config, dict) else _integration_context()
    for sensor_key in STUDY_SENSOR_KEYS:
        effective = _sensor_runtime_state()["effective"].get(sensor_key, False)
        try:
            if effective:
                initialize_plugin(sensor_key, context)
                run_runtime_action(sensor_key, "start", context)
            else:
                run_runtime_action(sensor_key, "stop", context)
                if sensor_key == "camera_emotion":
                    current_app.config["CAMERA_PREVIEW_ACTIVE"] = False
        except Exception:
            pass
    return jsonify({"ok": True, "sensor_runtime": _sensor_runtime_state()})


@bp.route("/api/admin/system/create-shortcut", methods=["POST"])
def admin_create_shortcut():
    try:
        return jsonify(create_desktop_shortcut(current_app.config))
    except ShortcutError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
