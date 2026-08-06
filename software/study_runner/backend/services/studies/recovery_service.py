"""Finds and finalizes study sessions orphaned by a crash or closed tab.

Three kinds of on-disk artifact can outlive a session that never reached a
normal save:

- ``_partial/<session_id>.json`` - the answers-so-far snapshot the tablet
  posts after every card (``routes/results.py::save_partial_results``).
- ``_flush/<session_id>_<suffix>.json`` - periodic sensor exports written
  every interval while the session was active (``sensor_flush_service.py``).
- ``_recovery/*.json`` - a raw, complete submission that reached the server
  but could not be saved (e.g. a full disk), from
  ``routes/results.py::_write_results_recovery_file``.

An artifact is a recovery candidate only when no saved result already
exists for its session - once finalized (or discarded), the source files
move under an archive subfolder so the same scan never surfaces them again.
Nothing is ever hard-deleted.

Like ``sessions_index_service``, there is no persistent index: candidates
are rare and the folders are small, so rescanning on every call is cheap
and can never drift from what is actually on disk.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import time
from pathlib import Path
from typing import Any

from study_runner.plugin_framework.plugin_api import PluginContext

from .results_service import (
    _project_root,
    _resolve_platform_value,
    _resolve_project_path,
    _write_signal_sidecar,
    build_answer_details,
    sanitize_identifier_for_filename,
    save_results_payload,
)
from ..recording.sensor_flush_service import discard_session_flush_files
from .sessions_index_service import list_sessions
from ..shared.validation import skipped_optional_questions_for_result

XDF_GRACE_SECONDS = 600
STUCK_FINISH_RECOVERY_SECONDS = 90


class RecoveryError(RuntimeError):
    """Raised for an unknown or already-handled recovery item."""


def list_recovery_candidates(
    data_dir: Path,
    hardware_config: dict[str, Any] | None = None,
    active_session_ids: set[str] | None = None,
    active_session_states: list[dict[str, Any]] | None = None,
    now_epoch: float | None = None,
    stuck_after_seconds: float = STUCK_FINISH_RECOVERY_SECONDS,
) -> list[dict[str, Any]]:
    """List sessions that need an operator decision.

    ``active_session_ids`` are sessions the persistent session store still
    considers live (not stale, not completed) - the tablet's own silent
    reconnect can still resume those normally, so a ``_partial`` snapshot
    for one is deliberately not surfaced yet, or every in-progress study
    would show a false "interrupted" warning while it is still running
    fine. A ``_recovery`` dump is different: it only exists because the
    participant already finished and the save itself failed, so it is
    always immediately actionable regardless of session-store state.

    ``active_session_states`` carries the same active sessions with their
    current card state. If an active session is already stuck on the finish
    card for long enough, it is surfaced as a recovery candidate instead of
    being hidden behind the normal resume path.
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return []

    already_saved = _saved_session_identities(data_dir)
    still_resumable = active_session_ids or set()
    stuck_active = set()
    if active_session_states is not None:
        still_resumable, stuck_active = recovery_session_sets(
            active_session_states,
            now_epoch=now_epoch,
            stuck_after_seconds=stuck_after_seconds,
        )

    candidates: list[dict[str, Any]] = []
    for study_dir in sorted(path for path in data_dir.iterdir() if path.is_dir() and not path.name.startswith("_")):
        study_id = study_dir.name
        candidates.extend(
            _partial_candidates(study_dir, study_id, already_saved, still_resumable, stuck_active, hardware_config)
        )
        candidates.extend(_recovery_dump_candidates(study_dir, study_id, already_saved, hardware_config))

    candidates.sort(key=lambda item: str(item.get("last_activity") or ""), reverse=True)
    return candidates


def _saved_session_identities(data_dir: Path) -> set[tuple[str, str]]:
    """Return deduplication identities without re-exposing legacy results.

    The Admin session browser intentionally indexes only canonical v3 session
    trees. Recovery has a narrower responsibility: it must not offer an old
    partial snapshot when a result was already written by the legacy flat
    saver. We therefore inspect those files only for their study/session IDs;
    they never enter the public session index or browser payload.
    """

    identities = {
        (str(session["study_id"]), str(session["session_id"]))
        for session in list_sessions(data_dir)
        if session.get("study_id") and session.get("session_id")
    }
    for study_dir in data_dir.iterdir():
        if not study_dir.is_dir() or study_dir.name.startswith("_"):
            continue
        for participant_dir in study_dir.iterdir():
            if (
                not participant_dir.is_dir()
                or participant_dir.name.startswith("_")
                or participant_dir.name == "participants"
            ):
                continue
            for result_path in participant_dir.glob("*.json"):
                payload = _read_json_object(result_path)
                if not payload or not isinstance(payload.get("answers"), dict):
                    continue
                session_id = str(payload.get("session_id") or "").strip()
                if session_id:
                    identities.add((str(payload.get("study_id") or study_dir.name), session_id))
    return identities


def recovery_session_sets(
    active_session_states: list[dict[str, Any]],
    *,
    now_epoch: float | None = None,
    stuck_after_seconds: float = STUCK_FINISH_RECOVERY_SECONDS,
) -> tuple[set[str], set[str]]:
    now = time.time() if now_epoch is None else float(now_epoch)
    still_resumable: set[str] = set()
    stuck_active: set[str] = set()
    for session in active_session_states:
        if not isinstance(session, dict):
            continue
        session_id = str(session.get("session_id") or "").strip()
        if not session_id:
            continue
        if _is_stuck_finish_session(session, now, stuck_after_seconds):
            stuck_active.add(session_id)
        else:
            still_resumable.add(session_id)
    return still_resumable, stuck_active


def finalize_recovery_candidate(
    data_dir: Path,
    config_data: dict[str, Any],
    hardware_config: dict[str, Any],
    context: PluginContext,
    recovery_id: str,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    kind, study_id, token = _parse_recovery_id(recovery_id)
    study_dir = data_dir / study_id
    source_path = study_dir / ("_partial" if kind == "partial" else "_recovery") / f"{token}.json"
    if not source_path.is_file():
        raise RecoveryError("This interrupted session was already handled.")

    try:
        raw_payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RecoveryError(f"Could not read the saved snapshot: {error}") from error
    if not isinstance(raw_payload, dict):
        raise RecoveryError("The saved snapshot is not valid.")

    session_id = str(raw_payload.get("session_id") or token)
    if kind == "partial":
        result_payload = {
            "session_id": session_id,
            "participant_id": raw_payload.get("participant_id"),
            "study_id": raw_payload.get("study_id") or study_id,
            "client_clock_offset_ms": raw_payload.get("client_clock_offset_ms"),
            "timestamp_start": raw_payload.get("timestamp_start"),
            "timestamp_end": raw_payload.get("snapshot_at") or raw_payload.get("server_received_at"),
            "answers": raw_payload.get("answers") if isinstance(raw_payload.get("answers"), dict) else {},
            "participant_metadata": (
                raw_payload.get("participant_metadata") if isinstance(raw_payload.get("participant_metadata"), dict) else {}
            ),
            "answer_events": raw_payload.get("answer_events") if isinstance(raw_payload.get("answer_events"), list) else [],
            "card_events": raw_payload.get("card_events") if isinstance(raw_payload.get("card_events"), list) else [],
        }
    else:
        result_payload = dict(raw_payload)
    result_payload["recovered"] = True

    # Deliberately not validate_and_normalize_results(): a crash-interrupted
    # session is inherently incomplete, and that gate exists to protect a
    # *live* participant submission, not to reject data recovery is meant
    # to rescue. build_answer_details() already tolerates missing answers.
    result_payload["skipped_questions"] = skipped_optional_questions_for_result(
        result_payload.get("answers") if isinstance(result_payload.get("answers"), dict) else {},
        config_data.get("questions", []),
        answer_events=result_payload.get("answer_events") if isinstance(result_payload.get("answer_events"), list) else [],
        card_events=result_payload.get("card_events") if isinstance(result_payload.get("card_events"), list) else [],
    )
    result_payload["answer_details"] = build_answer_details(
        result_payload,
        config_data,
        hardware_config,
        include_legacy_sensor_summaries=True,
    )
    result_payload["sensor_summary_provenance"] = {
        "classification": "legacy_recovery_noncanonical",
        "source": "runtime_memory",
        "canonical": False,
        "canonical_artifact": "card-summary.json",
    }

    saved_output = save_results_payload(data_dir, study_id, result_payload, hardware_config, context=context)
    _apply_flushed_sidecars(data_dir, study_id, session_id, result_payload, saved_output)
    discard_session_flush_files(data_dir, study_id, session_id)
    _archive_source(source_path, source_path.parent / "finalized")

    return {"result_payload": result_payload, "saved_output": saved_output}


def discard_recovery_candidate(data_dir: Path, recovery_id: str) -> dict[str, Any]:
    data_dir = Path(data_dir)
    kind, study_id, token = _parse_recovery_id(recovery_id)
    study_dir = data_dir / study_id
    source_path = study_dir / ("_partial" if kind == "partial" else "_recovery") / f"{token}.json"
    if not source_path.is_file():
        raise RecoveryError("This interrupted session was already handled.")

    session_id = token
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            session_id = str(payload.get("session_id") or token)
    except (OSError, ValueError):
        session_id = token
    if kind == "partial":
        discard_session_flush_files(data_dir, study_id, session_id)

    _archive_source(source_path, study_dir / "_recovery" / "discarded")
    return {"ok": True, "session_id": session_id}


def _partial_candidates(
    study_dir: Path,
    study_id: str,
    already_saved: set[tuple[str, str]],
    still_resumable: set[str],
    stuck_active: set[str],
    hardware_config: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    partial_dir = study_dir / "_partial"
    if not partial_dir.is_dir():
        return []
    flush_dir = study_dir / "_flush"

    candidates = []
    for path in sorted(partial_dir.glob("*.json")):
        payload = _read_json_object(path)
        if payload is None:
            continue
        session_id = str(payload.get("session_id") or path.stem)
        if (study_id, session_id) in already_saved:
            continue
        if session_id in still_resumable:
            continue
        answers = payload.get("answers")
        last_activity = payload.get("snapshot_at") or payload.get("server_received_at") or _mtime_iso(path)
        sensors_flushed = _flushed_sensor_names(flush_dir, session_id) if flush_dir.is_dir() else []
        candidates.append(
            {
                "recovery_id": f"partial:{sanitize_identifier_for_filename(study_id)}:{sanitize_identifier_for_filename(path.stem)}",
                "kind": "partial",
                "study_id": study_id,
                "session_id": session_id,
                "participant_hint": str(payload.get("participant_id") or ""),
                "last_activity": last_activity,
                "answers_count": len(answers) if isinstance(answers, dict) else 0,
                "sensors_flushed": sensors_flushed,
                "has_xdf_nearby": _has_xdf_nearby(hardware_config, _parse_epoch(last_activity)),
                "stuck_active": session_id in stuck_active,
            }
        )
    return candidates


def _is_stuck_finish_session(session: dict[str, Any], now_epoch: float, stuck_after_seconds: float) -> bool:
    if session.get("status") != "active":
        return False
    current_type = session.get("current_type")
    if current_type != "finish":
        events = session.get("events")
        last_event = events[-1] if isinstance(events, list) and events else {}
        current_type = last_event.get("current_type") if isinstance(last_event, dict) else None
    if current_type != "finish":
        return False
    try:
        last_seen = float(session.get("last_seen"))
    except (TypeError, ValueError):
        return False
    return now_epoch - last_seen >= stuck_after_seconds


def _recovery_dump_candidates(
    study_dir: Path,
    study_id: str,
    already_saved: set[tuple[str, str]],
    hardware_config: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    recovery_dir = study_dir / "_recovery"
    if not recovery_dir.is_dir():
        return []

    candidates = []
    for path in sorted(recovery_dir.glob("*.json")):
        payload = _read_json_object(path)
        if payload is None:
            continue
        session_id = str(payload.get("session_id") or path.stem)
        if (study_id, session_id) in already_saved:
            continue
        answers = payload.get("answers")
        last_activity = payload.get("timestamp_end") or payload.get("timestamp_start") or _mtime_iso(path)
        candidates.append(
            {
                "recovery_id": f"recovery_dump:{sanitize_identifier_for_filename(study_id)}:{sanitize_identifier_for_filename(path.stem)}",
                "kind": "recovery_dump",
                "study_id": study_id,
                "session_id": session_id,
                "participant_hint": str(payload.get("participant_id") or ""),
                "last_activity": last_activity,
                "answers_count": len(answers) if isinstance(answers, dict) else 0,
                "sensors_flushed": [],
                "has_xdf_nearby": _has_xdf_nearby(hardware_config, _parse_epoch(last_activity)),
            }
        )
    return candidates


def _flushed_sensor_names(flush_dir: Path, session_id: str) -> list[str]:
    names: list[str] = []
    for path in sorted(flush_dir.glob(f"{sanitize_identifier_for_filename(session_id)}_*.json")):
        payload = _read_json_object(path)
        sensor = payload.get("sensor") if payload else None
        if sensor and sensor not in names:
            names.append(str(sensor))
    return names


def _apply_flushed_sidecars(
    data_dir: Path,
    study_id: str,
    session_id: str,
    result_payload: dict[str, Any],
    saved_output: dict[str, Any],
) -> None:
    """Splice in pre-crash sensor data a live export cannot reach.

    ``save_results_payload`` already attempted a live sidecar export, which
    only sees history since *this* process started - empty right after a
    restart. This fills the gap from what ``sensor_flush_service`` saved
    just before the crash, reusing the same sidecar writer the live path
    uses so the resulting file is indistinguishable from a normal one.
    """
    flush_dir = Path(data_dir) / sanitize_identifier_for_filename(study_id) / "_flush"
    if not flush_dir.is_dir():
        return

    participant_dir = Path(data_dir).parent / saved_output["participant_dir"]
    safe_participant_id = sanitize_identifier_for_filename(str(result_payload.get("participant_id") or "participant"))

    for flush_path in sorted(flush_dir.glob(f"{sanitize_identifier_for_filename(session_id)}_*.json")):
        flush_payload = _read_json_object(flush_path)
        if flush_payload is None:
            continue
        output_key = str(flush_payload.get("output_key") or f"{flush_payload.get('sensor')}_file")
        if saved_output.get(output_key):
            continue  # a live export already produced this sidecar
        samples = flush_payload.get("samples")
        if not isinstance(samples, list) or not samples:
            continue
        suffix = str(flush_payload.get("filename_suffix") or flush_payload.get("sensor") or "signals")
        sidecar_path = _write_signal_sidecar(
            participant_dir,
            f"{safe_participant_id}_{suffix}",
            str(flush_payload.get("sensor") or "unknown"),
            result_payload,
            samples,
        )
        saved_output[output_key] = sidecar_path.relative_to(Path(data_dir).parent).as_posix()


def _archive_source(source_path: Path, archive_dir: Path) -> None:
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / source_path.name
        if target.exists():
            target = archive_dir / f"{source_path.stem}_{int(time.time())}{source_path.suffix}"
        shutil.move(str(source_path), str(target))
    except OSError as error:
        print(f"[RECOVERY] Could not archive {source_path.name}: {error}")


def _has_xdf_nearby(hardware_config: dict[str, Any] | None, since_epoch: float | None) -> bool:
    if not isinstance(hardware_config, dict) or since_epoch is None:
        return False
    labrecorder_config = hardware_config.get("labrecorder")
    if not isinstance(labrecorder_config, dict) or not labrecorder_config.get("enabled"):
        return False
    source_dir_value = _resolve_platform_value(labrecorder_config.get("xdf_source_dir"))
    if not source_dir_value:
        return False
    try:
        source_dir = _resolve_project_path(source_dir_value, _project_root())
    except Exception:
        return False
    if not source_dir.is_dir():
        return False
    try:
        return any(
            path.stat().st_mtime >= since_epoch - XDF_GRACE_SECONDS
            for path in source_dir.glob("*.xdf")
        )
    except OSError:
        return False


def _parse_recovery_id(recovery_id: str) -> tuple[str, str, str]:
    parts = str(recovery_id or "").split(":", 2)
    if len(parts) != 3 or parts[0] not in {"partial", "recovery_dump"}:
        raise RecoveryError("Unknown recovery item.")
    kind, study_id, token = parts
    if not study_id or not token:
        raise RecoveryError("Unknown recovery item.")
    if sanitize_identifier_for_filename(study_id) != study_id or sanitize_identifier_for_filename(token) != token:
        raise RecoveryError("Unknown recovery item.")
    return kind, study_id, token


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _mtime_iso(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
