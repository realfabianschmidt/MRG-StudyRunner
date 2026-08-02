"""Saving study results - the most protected path in the whole app.

Participant answers arrive exactly once. Every failure path here must
preserve the raw submission on disk (see _write_results_recovery_file)
and the partial-snapshot endpoint keeps a server-side copy of everything
answered so far in case the tablet dies before the final submit.
"""
import json
import time
import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..services.atomic_io import atomic_write_json
from ..services.finalization_service import SubmissionConflictError
from ..services.results_service import (
    build_answer_details,
    sanitize_canonical_submission_sensor_summaries,
    sanitize_identifier_for_filename,
)
from ..services.secrets_service import redact_hardware_config
from ..services.study_config_service import load_config
from ..services.upload_jobs_service import build_job_metadata
from ..services.validation import (
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
)
from .helpers import _complete_study_run, _runtime_hardware_config, _stop_study_session_tracking

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


def _is_empty_snapshot_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, (dict, list)):
        return len(value) == 0
    return False


def _merge_mapping_preserving_values(previous, incoming) -> dict:
    merged = dict(previous) if isinstance(previous, dict) else {}
    if not isinstance(incoming, dict):
        return merged
    for key, value in incoming.items():
        if _is_empty_snapshot_value(value) and not _is_empty_snapshot_value(merged.get(key)):
            continue
        merged[key] = value
    return merged


def _merge_event_list(previous, incoming) -> list:
    merged: list = []
    seen: set[str] = set()
    for event_list in (previous, incoming):
        if not isinstance(event_list, list):
            continue
        for item in event_list:
            try:
                marker = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
            except TypeError:
                marker = str(item)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(item)
    return merged


def _merge_current_index(previous, incoming):
    try:
        previous_index = int(previous)
        incoming_index = int(incoming)
    except (TypeError, ValueError):
        return incoming if incoming is not None else previous
    return max(previous_index, incoming_index)


def _merge_partial_snapshot(previous, incoming: dict) -> dict:
    if not isinstance(previous, dict):
        return dict(incoming)

    merged = dict(previous)
    for key, value in incoming.items():
        if key in {"answers", "participant_metadata", "answer_events", "card_events"}:
            continue
        if key == "current_index":
            merged[key] = _merge_current_index(previous.get(key), value)
            continue
        if _is_empty_snapshot_value(value) and not _is_empty_snapshot_value(previous.get(key)):
            continue
        merged[key] = value

    merged["answers"] = _merge_mapping_preserving_values(previous.get("answers"), incoming.get("answers"))
    merged["participant_metadata"] = _merge_mapping_preserving_values(
        previous.get("participant_metadata"),
        incoming.get("participant_metadata"),
    )
    merged["answer_events"] = _merge_event_list(previous.get("answer_events"), incoming.get("answer_events"))
    merged["card_events"] = _merge_event_list(previous.get("card_events"), incoming.get("card_events"))
    return merged


@bp.route("/api/results", methods=["POST"])
def save_results():
    result_payload = request.get_json() or {}
    try:
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        hardware_config = _runtime_hardware_config()
        validated_results = validate_and_normalize_results(result_payload, config_data)
        session_id = str(result_payload.get("session_id") or "").strip()
        if session_id:
            validated_results["session_id"] = session_id
        submission_id = str(validated_results.get("submission_id") or "").strip()
        validated_results["submission_id"] = submission_id or f"submission-{session_id}"
        validated_results["answer_details"] = build_answer_details(
            validated_results,
            config_data,
            hardware_config,
        )
        validated_results = sanitize_canonical_submission_sensor_summaries(validated_results)
        safe_hardware_config = redact_hardware_config(
            hardware_config,
            current_app.config.get("LOCAL_SECRETS", {}),
            str(config_data.get("study_id") or ""),
        )
        tracked_session = current_app.config["SESSION_STORE"].get(session_id) if session_id else None
        finalization_job = current_app.config["FINALIZATION_SERVICE"].commit_submission(
            validated_results,
            config_data=config_data,
            hardware_config=safe_hardware_config,
            recording_expected=_recording_expected(config_data),
            started_at_epoch=(tracked_session or {}).get("started_at_epoch"),
        )
    except ValidationError:
        _write_results_recovery_file(result_payload)
        raise
    except SubmissionConflictError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
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
    print(f"[DATA] Submission committed: {finalization_job['session_path']}")
    session_id = str(result_payload.get("session_id") or "")
    # The durable finalization commit above is the acknowledgement boundary.
    # Bookkeeping failures after that point must never turn a safely committed
    # submission into an HTTP 500 (which would keep the participant trapped on
    # the submit screen and invite needless retries).  The idempotent
    # finalization job remains authoritative and the admin can see these
    # warnings while the local cleanup is retried on the next request/restart.
    post_commit_warnings: list[str] = []
    try:
        session_completed = _stop_study_session_tracking(session_id)
    except Exception as error:
        session_completed = False
        post_commit_warnings.append(f"session_tracking: {error}")
        print(f"[DATA] Submission committed, but session tracking cleanup failed: {error}")
    try:
        run_state = _complete_study_run(config_data["study_id"], session_id)
    except Exception as error:
        run_state = None
        post_commit_warnings.append(f"study_run_state: {error}")
        print(f"[DATA] Submission committed, but study-run cleanup failed: {error}")
    _discard_partial_snapshot(result_payload)
    # XDF closing, validation, merge, statistics, and network destinations run
    # from the persistent job after this durable acknowledgement.
    return (
        jsonify(
            {
                "ok": True,
                "accepted": True,
                "finalization_job": finalization_job,
                "session_completed": session_completed,
                "study_run_state": run_state,
                "post_commit_warnings": post_commit_warnings,
            }
        ),
        202,
    )


def _recording_expected(config_data: dict) -> bool:
    settings = config_data.get("study_settings") or {}
    plugins = settings.get("plugins")
    if isinstance(plugins, dict):
        try:
            from study_runner.integrations.registry import get_plugin_manifests

            manifests = get_plugin_manifests()
        except Exception:
            manifests = {}
        for plugin_key, selection in plugins.items():
            if not isinstance(selection, dict) or not selection.get("enabled"):
                continue
            capabilities = (manifests.get(str(plugin_key)) or {}).get("capabilities") or {}
            if "recording_source" in capabilities:
                return True
        return False
    sensors = settings.get("sensors") or {}
    return bool(settings.get("sensors_enabled") and isinstance(sensors, dict) and any(sensors.values()))


def _enqueue_upload_jobs(
    validated_results: dict,
    config_data: dict,
    hardware_config: dict,
    saved_output: dict,
) -> tuple[list[dict], list[str]]:
    """Compatibility publication for pre-v2 recovery artifacts.

    Normal submissions publish only through the persistent finalization state
    machine. Crash snapshots created by the old flat-result path still call
    this helper so they can be rescued without pretending they have canonical
    source or merged XDF artifacts. No RAM-derived biosignal summary is added.
    """

    service = current_app.config["UPLOAD_JOBS_SERVICE"]
    study_settings = config_data.get("study_settings") or {}
    session_id = str(
        validated_results.get("session_id")
        or Path(str(saved_output.get("json_file") or "")).stem
        or uuid.uuid4()
    )
    safe_hardware_config = redact_hardware_config(
        hardware_config,
        current_app.config.get("LOCAL_SECRETS", {}),
        str(config_data.get("study_id") or ""),
    )
    job_payload = {
        "result_payload": validated_results,
        "hardware_config": safe_hardware_config,
        "saved_output": dict(saved_output),
        "config_data": config_data,
    }
    metadata = build_job_metadata(validated_results, saved_output)
    destinations = [
        (plugin_key, label)
        for plugin_key, label in (("notion", "Notion"), ("nextcloud", "Nextcloud"))
        if _destination_selected(study_settings, plugin_key)
    ]

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
            errors.append(f"{label}: {error}")
    return jobs, errors


def _destination_selected(settings: dict, plugin_key: str) -> bool:
    plugins = settings.get("plugins") if isinstance(settings, dict) else None
    selection = plugins.get(plugin_key) if isinstance(plugins, dict) else None
    if isinstance(selection, dict) and "enabled" in selection:
        return bool(selection.get("enabled"))
    return bool(settings.get(f"{plugin_key}_enabled")) if isinstance(settings, dict) else False


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
        previous_payload = None
        if snapshot_path.is_file():
            try:
                previous_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous_payload = None
        atomic_write_json(snapshot_path, _merge_partial_snapshot(previous_payload, payload))
        return jsonify({"ok": True})
    except Exception as error:
        print(f"[DATA] Could not write partial snapshot: {error}")
        return jsonify({"ok": False, "error": str(error)}), 500
