"""Background upload status, retries, and local result-folder actions."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..services.settings.folder_open_service import FolderOpenError, open_results_folder
from ..services.delivery.upload_jobs_service import (
    DEFAULT_STATUS_DAYS,
    MAX_STATUS_DAYS,
    UploadJobError,
)


bp = Blueprint("uploads", __name__)


def _service():
    return current_app.config["UPLOAD_JOBS_SERVICE"]


@bp.route("/api/uploads/status", methods=["GET"])
def upload_status():
    raw_days = request.args.get("days", str(DEFAULT_STATUS_DAYS))
    try:
        days = int(raw_days)
    except ValueError:
        return jsonify({"ok": False, "error": "days must be a whole number."}), 400
    if not 1 <= days <= MAX_STATUS_DAYS:
        return jsonify({"ok": False, "error": f"days must be between 1 and {MAX_STATUS_DAYS}."}), 400
    return jsonify(_service().status(days=days))


@bp.route("/api/uploads/retry", methods=["POST"])
def upload_retry():
    payload = request.get_json(silent=True) or {}
    job_id = str(payload.get("job_id") or "").strip()
    all_failed = payload.get("all_failed") is True
    if bool(job_id) == bool(all_failed):
        return jsonify({"ok": False, "error": "Send either job_id or all_failed: true."}), 400
    try:
        return jsonify(_service().retry(job_id=job_id, all_failed=all_failed))
    except UploadJobError as error:
        return jsonify({"ok": False, "error": str(error)}), 404


@bp.route("/api/admin/system/open-results-folder", methods=["POST"])
def open_results_folder_route():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            open_results_folder(
                current_app.config["DATA_DIR"],
                str(payload.get("study_id") or ""),
                str(payload.get("participant_id") or ""),
            )
        )
    except FolderOpenError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
