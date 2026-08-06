"""Operator endpoints for finalizing or discarding crash-orphaned sessions."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..services.studies.recovery_service import (
    RecoveryError,
    discard_recovery_candidate,
    finalize_recovery_candidate,
    list_recovery_candidates,
)
from ..services.studies.study_config_service import load_config
from ..services.shared.validation import validate_and_normalize_config
from .helpers import _plugin_context, _runtime_hardware_config, _stop_study_session_tracking
from .results import _enqueue_upload_jobs

bp = Blueprint("recovery", __name__)


@bp.route("/api/admin/recovery", methods=["GET"])
def admin_recovery_candidates():
    active_sessions = current_app.config["SESSION_STORE"].list_active()
    candidates = list_recovery_candidates(
        current_app.config["DATA_DIR"],
        _runtime_hardware_config(),
        active_session_states=active_sessions,
    )
    return jsonify({"ok": True, "candidates": candidates})


@bp.route("/api/admin/recovery/finalize", methods=["POST"])
def admin_recovery_finalize():
    payload = request.get_json(silent=True) or {}
    recovery_id = str(payload.get("recovery_id") or "").strip()
    if not recovery_id:
        return jsonify({"ok": False, "error": "recovery_id is required."}), 400

    try:
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        hardware_config = _runtime_hardware_config()
        result = finalize_recovery_candidate(
            current_app.config["DATA_DIR"],
            config_data,
            hardware_config,
            _plugin_context(),
            recovery_id,
        )
    except RecoveryError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
    except Exception as error:
        print(f"[RECOVERY] Finalize failed: {error}")
        return jsonify({"ok": False, "error": str(error)}), 500

    # Local save already succeeded at this point; a job-queue hiccup must
    # never turn a successful finalize into an error the operator has to redo.
    try:
        _enqueue_upload_jobs(result["result_payload"], config_data, hardware_config, result["saved_output"])
    except Exception as error:
        print(f"[RECOVERY] Could not queue uploads for the finalized session: {error}")
    _stop_study_session_tracking(str(result["result_payload"].get("session_id") or ""))

    return jsonify({"ok": True, **result["saved_output"]})


@bp.route("/api/admin/recovery/discard", methods=["POST"])
def admin_recovery_discard():
    payload = request.get_json(silent=True) or {}
    recovery_id = str(payload.get("recovery_id") or "").strip()
    if not recovery_id:
        return jsonify({"ok": False, "error": "recovery_id is required."}), 400
    try:
        result = discard_recovery_candidate(current_app.config["DATA_DIR"], recovery_id)
    except RecoveryError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
    _stop_study_session_tracking(str(result.get("session_id") or ""))
    return jsonify(result)
