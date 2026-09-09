"""Completed-session browser, timeline, and withdrawal endpoints."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from study_runner.runtime_core.delivery.withdrawal_service import WithdrawalError
from study_runner.runtime_core.studies.sessions_index_service import (
    DEFAULT_MAX_POINTS,
    SessionNotFoundError,
    list_sessions,
    load_session,
    load_signal_samples,
    resolve_session_root,
)


bp = Blueprint("sessions", __name__)


@bp.route("/api/admin/sessions", methods=["GET"])
def admin_sessions():
    return jsonify(list_sessions(current_app.config["DATA_DIR"]))


@bp.route("/api/admin/sessions/<study_id>/<participant_id>", methods=["GET"])
def admin_session(study_id: str, participant_id: str):
    if request.args.get("result_file") is not None:
        return jsonify({"ok": False, "error": "result_file is legacy; use session_id or session_folder."}), 400
    try:
        return jsonify(
            load_session(
                current_app.config["DATA_DIR"],
                study_id,
                participant_id,
                session_id=request.args.get("session_id"),
                session_folder=request.args.get("session_folder"),
            )
        )
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except SessionNotFoundError as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/sessions/<study_id>/<participant_id>/signals", methods=["GET"])
def admin_session_signals(study_id: str, participant_id: str):
    if request.args.get("result_file") is not None:
        return jsonify({"ok": False, "error": "result_file is legacy; use session_id or session_folder."}), 400
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
                session_id=request.args.get("session_id"),
                session_folder=request.args.get("session_folder"),
                max_points=max_points,
                start=_optional_float(request.args.get("start")),
                end=_optional_float(request.args.get("end")),
            )
        )
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except SessionNotFoundError as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/sessions/<study_id>/<participant_id>/withdraw", methods=["POST"])
def admin_session_withdraw(study_id: str, participant_id: str):
    """Withdraw one session's consent: delete its data, keep a tombstone.

    Package A3/5i. Irreversible, so the client-side confirmation (typing the
    session id) is repeated as a server-side check here rather than trusted
    -- a UI safeguard alone is not a validated boundary (CONTRIBUTING.md
    section on validating at every boundary). ``confirm_session_id`` must
    match the session this URL actually resolves to.
    """
    payload = request.get_json(silent=True) or {}
    try:
        session_root, session_id = resolve_session_root(
            current_app.config["DATA_DIR"],
            study_id,
            participant_id,
            session_id=request.args.get("session_id"),
            session_folder=request.args.get("session_folder"),
        )
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except SessionNotFoundError as error:
        return jsonify({"ok": False, "error": str(error)}), 404

    confirmation = str(payload.get("confirm_session_id") or "").strip()
    if confirmation != session_id:
        return (
            jsonify({"ok": False, "error": "confirm_session_id does not match this session."}),
            400,
        )

    try:
        state = current_app.config["WITHDRAWAL_SERVICE"].withdraw(
            session_id=session_id,
            session_root=session_root,
            reason=str(payload.get("reason") or ""),
            requested_by=str(payload.get("requested_by") or ""),
        )
    except WithdrawalError as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    return jsonify({"ok": True, **state})


def _optional_float(raw: str | None) -> float | None:
    """Absent means the whole recording; present but unparseable is an error."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"'{raw}' is not a valid epoch second.") from error
