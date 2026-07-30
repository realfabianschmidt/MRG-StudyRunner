"""Saving study results - the most protected path in the whole app.

Participant answers arrive exactly once. Every failure path here must
preserve the raw submission on disk (see _write_results_recovery_file)
and the partial-snapshot endpoint keeps a server-side copy of everything
answered so far in case the tablet dies before the final submit.
"""
import time
import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..services.atomic_io import atomic_write_json
from ..services.results_service import (
    build_answer_details,
    build_biosignal_summary,
    sanitize_identifier_for_filename,
    save_results_payload,
)
from ..services.secrets_service import redact_hardware_config
from ..services.study_config_service import load_config
from ..services.upload_jobs_service import build_job_metadata
from ..services.validation import (
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
)
from .helpers import _integration_context, _runtime_hardware_config

bp = Blueprint("results", __name__)


def _write_results_recovery_file(result_payload: dict) -> str | None:
    """Best-effort raw dump of a submission that could not be saved normally.

    Participant answers arrive exactly once; if anything in the save path
    fails, this keeps the raw payload on disk so no study data is lost.
    Must never raise: the caller is already handling an error.
    """
    try:
        study_id = sanitize_identifier_for_filename(str(result_payload.get("study_id") or "unknown-study"))
        participant_id = sanitize_identifier_for_filename(str(result_payload.get("participant_id") or "participant"))
        recovery_dir = current_app.config["DATA_DIR"] / study_id / "_recovery"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        recovery_path = recovery_dir / f"{participant_id}_{timestamp}_{uuid.uuid4().hex[:8]}.json"
        atomic_write_json(recovery_path, result_payload)
        print(f"[DATA] Raw submission preserved: {recovery_path}")
        return str(recovery_path)
    except Exception as recovery_error:
        print(f"[DATA] Could not write recovery file: {recovery_error}")
        return None


def _partial_snapshot_path(payload: dict):
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    study_id = sanitize_identifier_for_filename(str(payload.get("study_id") or "unknown-study"))
    safe_session = sanitize_identifier_for_filename(session_id)
    return current_app.config["DATA_DIR"] / study_id / "_partial" / f"{safe_session}.json"


def _discard_partial_snapshot(payload: dict) -> None:
    """Remove the incremental snapshot once the full results are safely on disk."""
    try:
        snapshot_path = _partial_snapshot_path(payload)
        if snapshot_path is not None and snapshot_path.is_file():
            snapshot_path.unlink()
    except Exception as cleanup_error:
        print(f"[DATA] Could not remove partial snapshot: {cleanup_error}")


@bp.route("/api/results", methods=["POST"])
def save_results():
    result_payload = request.get_json() or {}
    try:
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        hardware_config = _runtime_hardware_config()
        validated_results = validate_and_normalize_results(result_payload, config_data)
        validated_results["answer_details"] = build_answer_details(
            validated_results,
            config_data,
            hardware_config,
        )
        saved_output = save_results_payload(
            current_app.config["DATA_DIR"],
            config_data["study_id"],
            validated_results,
            hardware_config,
            context=_integration_context(),
        )
    except ValidationError:
        _write_results_recovery_file(result_payload)
        raise
    except Exception as error:
        recovery_file = _write_results_recovery_file(result_payload)
        print(f"[DATA] Saving results failed: {error}")
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(error),
                    "recovered_file": recovery_file,
                }
            ),
            500,
        )
    print(f"[DATA] Saved: {saved_output['json_file']}")
    if saved_output.get("xdf_file"):
        print(f"[DATA] XDF: {saved_output['xdf_file']}")
    _discard_partial_snapshot(result_payload)

    # The results are on disk at this point; network work is journaled locally
    # and the participant receives the response without waiting for it.
    try:
        _enqueue_upload_jobs(
            validated_results,
            config_data,
            hardware_config,
            saved_output,
        )
    except Exception as error:
        # Local results are already durable. A secondary bookkeeping failure
        # must never turn a successful participant submit into an HTTP 500.
        print(f"[UPLOADS] Could not prepare upload jobs after save: {error}")
    return jsonify({"ok": True, **saved_output})


def _enqueue_upload_jobs(
    validated_results: dict,
    config_data: dict,
    hardware_config: dict,
    saved_output: dict,
) -> tuple[list[dict], list[str]]:
    service = current_app.config["UPLOAD_JOBS_SERVICE"]
    study_settings = config_data.get("study_settings", {})
    session_id = str(
        validated_results.get("session_id")
        or Path(str(saved_output.get("json_file") or "")).stem
        or uuid.uuid4()
    )
    safe_hardware_config = redact_hardware_config(
        hardware_config,
        current_app.config.get("LOCAL_SECRETS", {}),
    )
    enriched_output = dict(saved_output)
    if study_settings.get("notion_enabled"):
        try:
            enriched_output["biosignal_summary"] = build_biosignal_summary(
                hardware_config,
                saved_output,
                context=_integration_context(),
            )
        except Exception as error:
            enriched_output["biosignal_summary"] = {}
            print(f"[UPLOADS] Could not build optional Notion biosignal summary: {error}")
    job_payload = {
        "result_payload": validated_results,
        "hardware_config": safe_hardware_config,
        "saved_output": enriched_output,
        "config_data": config_data,
    }
    metadata = build_job_metadata(validated_results, saved_output)
    destinations = []
    if study_settings.get("notion_enabled"):
        destinations.append(("notion", "Notion"))
    if study_settings.get("nextcloud_enabled"):
        destinations.append(("nextcloud", "Nextcloud"))

    jobs: list[dict] = []
    errors: list[str] = []
    for kind, label in destinations:
        try:
            jobs.append(
                service.enqueue(
                    kind=kind,
                    study_id=str(validated_results.get("study_id") or ""),
                    participant_id=str(validated_results.get("participant_id") or ""),
                    session_id=session_id,
                    label=label,
                    payload=job_payload,
                    metadata=metadata,
                )
            )
        except Exception as error:
            message = f"{label}: {error}"
            errors.append(message)
            print(f"[UPLOADS] Could not queue {message}")
    return jobs, errors


@bp.route("/api/results/partial", methods=["POST"])
def save_partial_results():
    """Incremental answer snapshot from the participant page.

    Written after every answered card (and on pagehide via sendBeacon)
    so a closed tab or a crashed tablet cannot lose the whole session.
    The successful final /api/results submit removes the snapshot.
    """
    payload = request.get_json(force=True, silent=True) or {}
    snapshot_path = _partial_snapshot_path(payload)
    if snapshot_path is None:
        return jsonify({"ok": False, "error": "session_id is required"}), 400
    try:
        payload["server_received_at"] = time.time()
        atomic_write_json(snapshot_path, payload)
        return jsonify({"ok": True})
    except Exception as error:
        print(f"[DATA] Could not write partial snapshot: {error}")
        return jsonify({"ok": False, "error": str(error)}), 500
