"""End a running study on the admin's word, whatever state the session is in.

The admin sees one thing -- the run status -- so "Abort" must work whenever
that status says ``running``. Two cases:

- A session is recording: freeze it the way a normal completion does, keep
  every captured file, and leave the ``admin_abort`` tombstone
  (WithdrawalService.abort).
- Nothing is recording (the tablet's session start failed, the study has no
  recording sensors, or the server restarted mid-run): there is no data to
  freeze, so only the run and any still-open session tracking are closed.

Only when neither a recording nor a running run exists is there nothing to
abort. Stopping sensors stays with the caller, which owns the plugin context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from study_runner.runtime_core.delivery.withdrawal_service import WithdrawalError
from study_runner.runtime_core.studies.study_run_state_service import RUNNING_STATUS


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
) -> StudyRunAbortResult:
    reason = str(reason or "").strip()
    if not reason:
        raise StudyRunAbortError("A reason is required to abort a study.", 400)

    current = recording_runtime.current_status() if recording_runtime is not None else None
    if current is not None:
        session_id = str(current.get("session_id") or "")
        paths = recording_runtime.find_paths(session_id) if session_id else None
        if paths is None:
            raise StudyRunAbortError("Could not locate the active session on disk.", 404)
        try:
            withdrawal = withdrawal_service.abort(
                session_id=session_id,
                session_root=paths.root,
                reason=reason,
                requested_by=requested_by or "admin",
            )
        except WithdrawalError as error:
            raise StudyRunAbortError(str(error), 500) from error
        run_state = run_state_store.abort(reason=reason)
        return StudyRunAbortResult("recording", run_state, withdrawal=withdrawal)

    run_state = run_state_store.public()
    if run_state.get("status") != RUNNING_STATUS:
        raise StudyRunAbortError("No study is currently running.", 404)
    study_id = str(run_state.get("study_id") or "")
    closed = [
        str(session.get("session_id") or "")
        for session in session_store.list_active()
        if not study_id or str(session.get("study_id") or "") == study_id
    ]
    for session_id in closed:
        session_store.mark_completed(session_id)
    return StudyRunAbortResult(
        "run_only",
        run_state_store.abort(reason=reason),
        closed_sessions=[session_id for session_id in closed if session_id],
    )
