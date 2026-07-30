"""Endpoints the participant tablet talks to during a study.

Study configuration, study sessions (start/stop/resume, lifecycle
events), sensor trial triggers (start/stop/marker), heartbeat, and the
clock-sync endpoint used to align tablet timestamps with the server.
"""
import time

from flask import Blueprint, current_app, jsonify, request

from ..services.study_client_service import register_heartbeat
from ..services.study_config_service import load_config, save_config, save_study
from ..services.trial_service import send_trial_marker, start_trial_session, stop_trial_session
from ..services.validation import validate_and_normalize_config, validate_and_normalize_trial_options
from .helpers import (
    _current_config_data,
    _public_study_session,
    _record_study_client_event,
    _refresh_trial_runtime,
    _resume_study_session,
    _sensor_runtime_state,
    _session_overrides,
    _start_or_reuse_study_session,
    _start_study_camera_monitor_runtime,
    _start_study_sensor_runtime,
    _stop_study_session_tracking,
    _stop_study_sensor_runtime,
    _valid_participant_id,
)

bp = Blueprint("study", __name__)


@bp.route("/api/config")
def get_config():
    config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
    config_data["_capabilities"] = {
        "unsafe_stimulus_code": bool(current_app.config.get("ALLOW_UNSAFE_STIMULUS_CODE", False))
    }
    config_data["_runtime"] = {
        "sensor_runtime": _sensor_runtime_state(config_data.get("study_settings", {})),
        "session_overrides": _session_overrides(),
    }
    return jsonify(config_data)


@bp.route("/api/config", methods=["POST"])
def update_config():
    config_data = request.get_json() or {}
    validated_config = validate_and_normalize_config(config_data)
    save_config(current_app.config["CONFIG_FILE"], validated_config)
    save_study(current_app.config["SAVED_STUDIES_DIR"], validated_config)
    print("[CONFIG] Saved.")
    return jsonify({"ok": True})


@bp.route("/api/study/session/start", methods=["POST"])
def start_study_session():
    payload = request.get_json() or {}
    if not _valid_participant_id(payload.get("participant_id")):
        return jsonify({"ok": False, "error": "Participant ID is required before a study can start."}), 400
    config_data = _current_config_data()
    session = _start_or_reuse_study_session(payload)
    result = _start_study_sensor_runtime(config_data.get("study_settings", {}))
    return jsonify({"ok": True, "session": _public_study_session(session), **result})


@bp.route("/api/study/session/stop", methods=["POST"])
def stop_study_session():
    payload = request.get_json() or {}
    session_id = str(payload.get("session_id") or "").strip()
    _stop_study_session_tracking(session_id)
    result = _stop_study_sensor_runtime()
    return jsonify({"ok": True, **result})


@bp.route("/api/study/session/resume", methods=["POST"])
def resume_study_session():
    payload = request.get_json() or {}
    if not _valid_participant_id(payload.get("participant_id")):
        return jsonify({"ok": False, "error": "Participant ID is required before a study can resume."}), 400
    session = _resume_study_session(payload)
    if session is None:
        return jsonify({"ok": False, "error": "No active study session was found for this tablet."}), 404
    return jsonify({"ok": True, "session": _public_study_session(session), "sensor_runtime": _sensor_runtime_state()})


@bp.route("/api/study/session/client-event", methods=["POST"])
def study_session_client_event():
    payload = request.get_json() or {}
    return jsonify({"ok": True, **_record_study_client_event(payload)})


@bp.route("/api/study/runtime")
def study_runtime():
    return jsonify(
        {
            "ok": True,
            "sensor_runtime": _sensor_runtime_state(),
            "session_overrides": _session_overrides(),
            "active_study_session": bool(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")),
            "camera_live_active": bool(current_app.config.get("CAMERA_PREVIEW_ACTIVE", False)),
        }
    )


@bp.route("/api/study/camera-monitor/start", methods=["POST"])
def start_study_camera_monitor():
    try:
        result = _start_study_camera_monitor_runtime()
        return jsonify({"ok": True, "runtime": result})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/api/start", methods=["POST"])
def start_trial():
    _refresh_trial_runtime()
    result = start_trial_session(validate_and_normalize_trial_options(request.get_json()))
    return jsonify({"ok": True, **result})


@bp.route("/api/stop", methods=["POST"])
def stop_trial():
    _refresh_trial_runtime()
    result = stop_trial_session(validate_and_normalize_trial_options(request.get_json()))
    return jsonify({"ok": True, **result})


@bp.route("/api/marker", methods=["POST"])
def trial_marker():
    _refresh_trial_runtime()
    payload = request.get_json() or {}
    options = validate_and_normalize_trial_options(payload)
    event = options.get("marker_event") or payload.get("event") or payload.get("phase") or "marker"
    result = send_trial_marker(str(event), options)
    return jsonify({"ok": True, **result})


@bp.route("/api/study-client/heartbeat", methods=["POST"])
def study_client_heartbeat():
    payload = request.get_json() or {}
    heartbeat_result = register_heartbeat(payload, request.remote_addr, request.headers.get("User-Agent", ""))
    return jsonify({"ok": True, **heartbeat_result, "sensor_runtime": _sensor_runtime_state()})


@bp.route("/api/sync-clock", methods=["POST"])
def sync_clock():
    """Clock-sync endpoint for tablet trigger precision against the Study Runner server."""
    data = request.get_json(force=True) or {}
    server_receive_ms = time.time() * 1000
    return jsonify(
        {
            "client_send_ms": data.get("client_send_ms"),
            "server_receive_ms": server_receive_ms,
            "server_send_ms": time.time() * 1000,
        }
    )
