"""Read-only completed-session browser and timeline endpoints."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..services.sessions_index_service import (
    DEFAULT_MAX_POINTS,
    SessionNotFoundError,
    list_sessions,
    load_session,
    load_signal_samples,
)


bp = Blueprint("sessions", __name__)


@bp.route("/api/admin/sessions", methods=["GET"])
def admin_sessions():
    return jsonify(list_sessions(current_app.config["DATA_DIR"]))


@bp.route("/api/admin/sessions/<study_id>/<participant_id>", methods=["GET"])
def admin_session(study_id: str, participant_id: str):
    try:
        return jsonify(
            load_session(
                current_app.config["DATA_DIR"],
                study_id,
                participant_id,
                result_file=request.args.get("result_file"),
            )
        )
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except SessionNotFoundError as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/sessions/<study_id>/<participant_id>/signals", methods=["GET"])
def admin_session_signals(study_id: str, participant_id: str):
    sensor = str(request.args.get("sensor") or "").strip()
    if not sensor:
        return jsonify({"ok": False, "error": "sensor is required."}), 400
    try:
        max_points = int(request.args.get("max_points", DEFAULT_MAX_POINTS))
        return jsonify(
            load_signal_samples(
                current_app.config["DATA_DIR"],
                study_id,
                participant_id,
                sensor,
                result_file=request.args.get("result_file"),
                max_points=max_points,
            )
        )
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except SessionNotFoundError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
