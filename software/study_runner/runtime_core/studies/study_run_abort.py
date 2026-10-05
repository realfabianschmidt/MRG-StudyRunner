"""End a running study on the admin's word, whatever state the session is in.

The admin sees one thing -- the run status -- so "Abort" must work whenever
that status says ``running`` or ``aborting``. Two cases:

- A session is recording: freeze it the way a normal completion does, keep
  every captured file, and leave the ``admin_abort`` tombstone
  (WithdrawalService.abort).
- Nothing is recording (the tablet's session start failed, the study has no
  recording sensors, or the server restarted mid-run): there is no data to
  freeze, so only the run and any still-open session tracking are closed.

Only when neither a recording nor a running run exists is there nothing to
abort. Sensors keep their study-level stream running; the caller closes the
participant-session scope after the trial and recorder stop is confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from study_runner.runtime_core.studies.study_run_state_service import ABORTING_STATUS, ABORTED_STATUS, RUNNING_STATUS


class StudyRunAbortError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class StudyRunAbortResult:
    outcome: str  # "recording" or "run_only"
    run_state: dict[str, Any]
    withdrawal: dict[str, Any] | None = None
    closed_sessions: list[str] = field(default_factory=list)


def abort_study_run(
    *,
    reason: str,
    requested_by: str,
    run_state_store: Any,
    session_store: Any,
    recording_runtime: Any | None,
    withdrawal_service: Any,
    trial_event_service: Any | None = None,
    trial_stopper: Any | None = None,
) -> StudyRunAbortResult:
    reason = str(reason or "").strip()
    if not reason:
        raise StudyRunAbortError("A reason is required to abort a study.", 400)

    run_state = run_state_store.public()
    if run_state.get("status") == ABORTED_STATUS:
        return StudyRunAbortResult("already_aborted", run_state)
    if run_state.get("status") not in {RUNNING_STATUS, ABORTING_STATUS}:
        raise StudyRunAbortError("No study is currently running.", 404)
    if run_state.get("status") == ABORTING_STATUS and reason != run_state.get("aborted_reason"):
        raise StudyRunAbortError("Retry the abort with its original reason.", 409)
    run_state_store.begin_abort(reason)
    study_id = str(run_state.get("study_id") or "")
    run_started = float(run_state.get("started_at_epoch") or 0)
    active = [
        session
        for session in session_store.list_uncompleted()
        if (not study_id or str(session.get("study_id") or "") == study_id)
        and float(session.get("started_at_epoch") or 0) >= run_started - 1
    ]
    current = recording_runtime.current_status() if recording_runtime is not None else None
    if current and current.get("status") == "frozen":
        current = None
    recorded = [
        recording_runtime.status_for_session(str(item.get("session_id") or ""))
        for item in active
    ] if recording_runtime is not None else []
    recorded = [item for item in recorded if item and item.get("status") not in {None, "frozen", "completed"}]
    try:
        if current and str(current.get("session_id") or "") not in {str(item.get("session_id") or "") for item in active}:
            raise RuntimeError("An active recording cannot be matched to an open session.")
        if len(recorded) > 1:
            raise RuntimeError("Multiple open recordings require operator review before abort can finish.")
        current = current or (recorded[0] if recorded else None)
        recording_session = str((current or {}).get("session_id") or "")
        for session in active:
            if trial_event_service is not None:
                _stop_session_trials(trial_event_service, trial_stopper, str(session.get("session_id") or ""))
        withdrawal = None
        if current is not None:
            paths = recording_runtime.find_paths(recording_session)
            if paths is None:
                raise RuntimeError("Could not locate the active recording on disk.")
            withdrawal = withdrawal_service.abort(
                session_id=recording_session,
                session_root=paths.root,
                reason=reason,
                requested_by=requested_by or "admin",
            )
        closed = [str(session.get("session_id") or "") for session in active]
        for session_id in closed:
            session_store.mark_completed(session_id)
        result = run_state_store.abort(reason=reason)
        return StudyRunAbortResult(
            "recording" if withdrawal else "run_only",
            result,
            withdrawal=withdrawal,
            closed_sessions=[session_id for session_id in closed if session_id],
        )
    except Exception as error:
        run_state_store.record_abort_error(str(error))
        raise StudyRunAbortError(f"Abort not confirmed: {error}", 503) from error


def _stop_session_trials(service: Any, stopper: Any, session_id: str) -> None:
    if not session_id:
        return
    state = service.snapshot()
    for stimulus_id, prepared in (state.get("preparations") or {}).items():
        if prepared.get("session_id") != session_id:
            continue
        start_id = str(prepared["event_id"])
        stop_id = str(prepared["stop_event_id"])
        start = (state.get("events") or {}).get(start_id)
        if start is None:
            service.cancel_preparation(start_id, stimulus_id, "abort")
            continue
        old_stop = (state.get("events") or {}).get(stop_id)
        if not old_stop or old_stop.get("status") != "done":
            if stopper is None:
                raise RuntimeError("Trial stopper is not configured.")
            payload = (old_stop or {}).get("payload")
            if old_stop and not payload:
                raise RuntimeError(f"Trial stop {stop_id} cannot be replayed safely.")
            if not payload:
                receipt_ms = round(time.time() * 1000.0, 3)
                payload = {
                    "event_id": stop_id,
                    "stimulus_id": stimulus_id,
                    "session_id": session_id,
                    "study_id": prepared.get("study_id"),
                    "participant_id": prepared.get("participant_id"),
                    "client_id": prepared.get("client_id"),
                    "question_index": prepared.get("question_index"),
                    "question_type": "stimulus",
                    "phase": "stimulus_admin_abort",
                    "marker_event": "stimulus_admin_abort",
                    "server_received_epoch_ms": receipt_ms,
                    "source_epoch_ms": receipt_ms,
                    "time_source": "server_receipt",
                }
            service.execute(stop_id, "trial_stop", payload, stopper)
        service.cancel_deadline(stimulus_id)
