"""Saving study results - the most protected path in the whole app.

Participant answers arrive exactly once. Every failure path here must
preserve the raw submission on disk (see _write_results_recovery_file)
and the partial-snapshot endpoint keeps a server-side copy of everything
answered so far in case the tablet dies before the final submit.
"""
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

from study_runner.runtime_core.studies.card_extension_bridge import CardExtensionUnavailableError, validate_card_answer

from flask import Blueprint, current_app, jsonify, request

from study_runner.plugin_framework.registry import (
    get_plugin_manifest,
    get_plugins_with_capability,
)
from study_runner.shared.atomic_io import atomic_write_json
from study_runner.shared.filename_sanitizer import sanitize_identifier_for_filename
from study_runner.data_core.host.artifacts import study_work_dir
from study_runner.data_core.host.sensor_flush_service import discard_session_flush_files
from study_runner.runtime_core.delivery.finalization_service import SubmissionConflictError
from study_runner.runtime_core.studies.results_service import (
    build_answer_details,
    sanitize_canonical_submission_sensor_summaries,
)
from study_runner.runtime_core.settings.secrets_service import redact_hardware_config
from study_runner.runtime_core.studies.study_config_service import load_config
from study_runner.runtime_core.studies.study_config_service import study_config_revision
from study_runner.runtime_core.delivery.upload_jobs_service import build_job_metadata
from study_runner.runtime_core.studies.validation import (
    NON_ANSWER_QUESTION_TYPES,
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
)
from .helpers import _complete_study_run, _runtime_hardware_config, _stop_study_session_tracking

bp = Blueprint("results", __name__)
_partial_snapshot_lock = threading.RLock()


def _write_results_recovery_file(result_payload: dict) -> str | None:
    """Best-effort raw dump of a submission that could not be saved normally.

    Participant answers arrive exactly once; if anything in the save path
    fails, this keeps the raw payload on disk so no study data is lost.
    Must never raise: the caller is already handling an error.
    """
    try:
        study_id = str(result_payload.get("study_id") or "unknown-study")
        participant_id = sanitize_identifier_for_filename(str(result_payload.get("participant_id") or "participant"))
        recovery_dir = study_work_dir(current_app.config["DATA_DIR"], study_id, "recovery")
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
    study_id = str(payload.get("study_id") or "unknown-study")
    safe_session = sanitize_identifier_for_filename(session_id)
    return study_work_dir(current_app.config["DATA_DIR"], study_id, "partial") / f"{safe_session}.json"


def _discard_partial_snapshot(payload: dict) -> None:
    """Remove the incremental snapshot once the full results are safely on disk.

    The ``_work/partial`` folder goes too once it is empty, so a finished study
    leaves no empty helper folders behind.
    """
    try:
        snapshot_path = _partial_snapshot_path(payload)
        if snapshot_path is not None:
            if snapshot_path.is_file():
                snapshot_path.unlink()
            _remove_empty_dir(snapshot_path.parent)
    except Exception as cleanup_error:
        print(f"[DATA] Could not remove partial snapshot: {cleanup_error}")


def _remove_empty_dir(directory: Path) -> None:
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass


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


def _verified_checkpoint(payload: dict) -> tuple[dict | None, str | None]:
    """Keep new checkpoints bound to one active session and study document."""

    session_id = str(payload.get("session_id") or "").strip()
    session = current_app.config["SESSION_STORE"].get(session_id)
    if not session or session.get("status") != "active":
        return None, "The study session is no longer active."
    for field in ("session_id", "study_id", "participant_id", "client_id", "study_revision"):
        expected = str(session.get(field) or "").strip()
        supplied = str(payload.get(field) or "").strip()
        if not expected or supplied != expected:
            return None, f"Checkpoint {field} does not match the active session."
    config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
    if study_config_revision(config_data) != session["study_revision"]:
        return None, "The active study changed after this session started."
    sequence = payload.get("checkpoint_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        return None, "checkpoint_sequence must be a positive integer."
    if not isinstance(payload.get("answers"), dict):
        return None, "Checkpoint answers must be an object."
    completed = payload.get("completed_indices")
    questions = config_data.get("questions") or []
    if not isinstance(completed, list) or any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(questions)
        for index in completed
    ):
        return None, "completed_indices must list valid card indices."
    if len(set(completed)) != len(completed):
        return None, "completed_indices must not contain duplicates."
    current_index = payload.get("current_index")
    if isinstance(current_index, bool) or not isinstance(current_index, int) or not 0 <= current_index < len(questions):
        return None, "current_index is invalid."
    answers = payload["answers"]
    completed_set = set(completed)
    for key, answer in answers.items():
        if not isinstance(key, str) or not key.startswith("q") or not key[1:].isdigit():
            return None, "Checkpoint has an unknown answer key."
        index = int(key[1:])
        if index not in completed_set or index >= len(questions):
            return None, "Checkpoint includes an unconfirmed Card answer."
        question = questions[index]
        if question["type"] in NON_ANSWER_QUESTION_TYPES:
            return None, "Checkpoint includes an answerless Card value."
        try:
            validate_card_answer(question["type"], question, answer, index + 1)
        except (ValueError, CardExtensionUnavailableError) as error:
            return None, f"Checkpoint answer {key} is invalid: {error}"
    for index in completed_set:
        question = questions[index]
        if question["type"] not in NON_ANSWER_QUESTION_TYPES and question.get("required", True) and f"q{index}" not in answers:
            return None, f"Completed Card {index} has no required answer."
    if not isinstance(payload.get("touched_fields"), dict) or not isinstance(payload.get("question_metrics"), dict):
        return None, "Checkpoint card state is incomplete."
    return session, None


def load_verified_partial_checkpoint(session: dict) -> dict | None:
    """Return only a v2 checkpoint for the exact active session on resume."""

    path = _partial_snapshot_path(session)
    if path is None:
        return None
    with _partial_snapshot_lock:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    if not isinstance(payload, dict) or payload.get("checkpoint_version") != 2:
        return None
    if any(str(payload.get(field) or "") != str(session.get(field) or "") for field in (
        "session_id", "study_id", "participant_id", "client_id", "study_revision",
    )):
        return None
    return payload


@bp.route("/api/results", methods=["POST"])
def save_results():
    result_received_epoch_ms = round(time.time() * 1000.0, 3)
    result_payload = request.get_json() or {}
    session_id = str(result_payload.get("session_id") or "").strip() if isinstance(result_payload, dict) else ""
    tracked_session = current_app.config["SESSION_STORE"].get(session_id) if session_id else None
    source_hash = _source_submission_sha256(result_payload) if isinstance(result_payload, dict) else ""
    submitted_study_id = str(result_payload.get("study_id") or "").strip() if isinstance(result_payload, dict) else ""
    submitted_participant = str(result_payload.get("participant_id") or "").strip() if isinstance(result_payload, dict) else ""
    committed_participant = str((tracked_session or {}).get("participant_id") or submitted_participant).strip()
    submission_id = str(result_payload.get("submission_id") or "").strip() if isinstance(result_payload, dict) else ""
    submission_id = submission_id or f"submission-{session_id}"
    if submitted_study_id and committed_participant and session_id:
        try:
            acknowledged = current_app.config["FINALIZATION_SERVICE"].acknowledged_source_submission(
                study_id=submitted_study_id,
                participant_id=committed_participant,
                submission_id=submission_id,
                session_id=session_id,
                source_submission_sha256=source_hash,
            )
        except SubmissionConflictError as error:
            return jsonify({"ok": False, "error": str(error)}), 409
        if acknowledged is not None:
            _discard_partial_snapshot(result_payload)
            return jsonify({
                "ok": True,
                "accepted": True,
                "finalization_job": acknowledged,
                "session_completed": bool((tracked_session or {}).get("status") == "completed"),
                "post_commit_warnings": [],
            }), 202
    if current_app.config["STUDY_RUN_STATE"].public().get("status") == "aborting":
        return jsonify({"ok": False, "code": "study_aborting", "error": "The study is being aborted. Contact the supervisor."}), 409
    try:
        config_data = validate_and_normalize_config(load_config(current_app.config["CONFIG_FILE"]))
        current_revision = study_config_revision(config_data)
        session_revision = str((tracked_session or {}).get("study_revision") or "").strip().lower()
        submitted_revision = str(result_payload.get("study_revision") or "").strip().lower()
        if session_revision and submitted_revision and session_revision != submitted_revision:
            return _study_revision_conflict(result_payload, current_revision)
        if (session_revision or submitted_revision) and (session_revision or submitted_revision) != current_revision:
            return _study_revision_conflict(result_payload, current_revision)
        hardware_config = _runtime_hardware_config()
        validated_results = validate_and_normalize_results(result_payload, config_data)
        validated_results["study_revision"] = current_revision
        end_event = validated_results.get("study_end_event") or {}
        if end_event:
            end_event["server_received_epoch_ms"] = result_received_epoch_ms
            if end_event.get("time_source") == "tablet_sync":
                age = end_event.get("clock_sync_age_ms")
                rtt = end_event.get("clock_sync_rtt_ms")
                source = end_event.get("source_epoch_ms")
                if (not end_event.get("clock_sync_id") or age is None or age > 120_000
                        or rtt is None or rtt > 10_000 or source is None
                        or source > result_received_epoch_ms + max(5000, rtt / 2 + 1000)):
                    end_event["client_claimed_source_epoch_ms"] = source
                    end_event["source_epoch_ms"] = result_received_epoch_ms
                    end_event["time_source"] = "server_receipt"
                    end_event["clock_quality"] = "invalid_or_stale_evidence"
                else:
                    end_event["clock_quality"] = "fresh"
            elif end_event.get("time_source") == "server_receipt":
                end_event["source_epoch_ms"] = result_received_epoch_ms
                end_event["clock_quality"] = "server_arrival_fallback"
            else:
                end_event["time_source"] = "legacy_unverified"
                end_event["clock_quality"] = "legacy_unverified"
        if session_id:
            validated_results["session_id"] = session_id
        validated_results["submission_id"] = submission_id
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
        tracked_participant = str((tracked_session or {}).get("participant_id") or "").strip()
        submitted_participant = str(validated_results.get("participant_id") or "").strip()
        if tracked_participant and submitted_participant and tracked_participant != submitted_participant:
            # The recording folder was reserved for the ID the session started
            # with. Keep the answers with that recording; record the edit.
            validated_results["participant_id_submitted"] = submitted_participant
            validated_results["participant_id"] = tracked_participant
        finalization_job = current_app.config["FINALIZATION_SERVICE"].commit_submission(
            validated_results,
            config_data=config_data,
            hardware_config=safe_hardware_config,
            recording_expected=_recording_expected(config_data),
            started_at_epoch=(tracked_session or {}).get("started_at_epoch"),
            source_submission_sha256=source_hash,
        )
    except (ValidationError, CardExtensionUnavailableError):
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
    # Flush files are crash insurance only; the canonical recording is on disk.
    discard_session_flush_files(
        current_app.config["DATA_DIR"],
        str(result_payload.get("study_id") or config_data.get("study_id") or ""),
        session_id,
    )
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


def _source_submission_sha256(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _study_revision_conflict(payload: dict, current_revision: str):
    recovery_file = _write_results_recovery_file(payload)
    return jsonify({
        "ok": False,
        "code": "study_revision_conflict",
        "error": "The study changed after this session started. The submission was preserved for recovery.",
        "current_revision": current_revision,
        "recovered_file": recovery_file,
    }), 409


def _recording_expected(config_data: dict) -> bool:
    settings = config_data.get("study_settings") or {}
    plugins = settings.get("plugins")
    if isinstance(plugins, dict):
        try:
            from study_runner.plugin_framework.registry import get_plugin_manifests

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
    destinations = []
    for plugin in get_plugins_with_capability("upload_destination"):
        if not _destination_selected(study_settings, plugin.key):
            continue
        manifest = get_plugin_manifest(plugin.key)
        label = str((manifest.get("ui") or {}).get("label") or plugin.label or plugin.key)
        destinations.append((plugin.key, label))

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
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "A JSON object is required."}), 400
    snapshot_path = _partial_snapshot_path(payload)
    if snapshot_path is None:
        return jsonify({"ok": False, "error": "session_id is required"}), 400
    try:
        if payload.get("checkpoint_version") not in (None, 2):
            return jsonify({"ok": False, "code": "checkpoint_version_unsupported", "error": "Unsupported checkpoint version."}), 400
        is_checkpoint = payload.get("checkpoint_version") == 2
        if is_checkpoint:
            _, error = _verified_checkpoint(payload)
            if error:
                return jsonify({"ok": False, "code": "checkpoint_invalid", "error": error}), 409
        with _partial_snapshot_lock:
            previous_payload = None
            if snapshot_path.is_file():
                try:
                    previous_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    previous_payload = None
            if is_checkpoint:
                if isinstance(previous_payload, dict) and previous_payload.get("checkpoint_version") == 2:
                    previous_sequence = previous_payload.get("checkpoint_sequence", 0)
                    sequence = payload["checkpoint_sequence"]
                    if sequence < previous_sequence:
                        return jsonify({"ok": False, "code": "checkpoint_stale", "error": "A newer checkpoint is already saved."}), 409
                    if sequence == previous_sequence:
                        comparable = dict(previous_payload)
                        comparable.pop("server_received_at", None)
                        if comparable != payload:
                            return jsonify({"ok": False, "code": "checkpoint_conflict", "error": "Checkpoint sequence was reused with different data."}), 409
                        return jsonify({"ok": True, "checkpoint_sequence": sequence, "duplicate": True})
                payload["server_received_at"] = time.time()
                atomic_write_json(snapshot_path, payload)
                return jsonify({"ok": True, "checkpoint_sequence": payload["checkpoint_sequence"]})
            if isinstance(previous_payload, dict) and previous_payload.get("checkpoint_version") == 2:
                return jsonify({"ok": False, "code": "checkpoint_stale", "error": "Legacy snapshots cannot replace a confirmed checkpoint."}), 409
            payload["server_received_at"] = time.time()
            atomic_write_json(snapshot_path, _merge_partial_snapshot(previous_payload, payload))
            return jsonify({"ok": True})
    except Exception as error:
        print(f"[DATA] Could not write partial snapshot: {error}")
        return jsonify({"ok": False, "error": str(error)}), 500
