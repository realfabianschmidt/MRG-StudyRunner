"""Durable, idempotent trial events and server-side stimulus deadlines.

The browser is a useful clock display, but it is not a reliable scheduler: mobile
browsers throttle callbacks in background tabs and requests can be retried after a
temporary network loss.  This service gives every start/stop/marker command a
stable identity and stores the result before it is acknowledged.  It also owns the
server-side stop deadline so recording cannot silently continue just because a
tablet callback arrived late.

The journal deliberately sits above the integration plugins.  Plugins receive the
same ``event_id``/``stimulus_id`` in their options and can forward those identifiers
to the recording worker, whose command protocol is idempotent as well.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import uuid
from typing import Any

from study_runner.shared.atomic_io import atomic_write_json

from .session_journal_service import (
    SessionJournalCorruptionError,
    SessionJournalStore,
    UNBOUND_SESSION_ID,
    _record_order as _journal_record_order,
)


JournalHandler = Callable[[dict[str, Any]], dict[str, Any] | None]
TimerFactory = Callable[[float, Callable[[], None]], threading.Timer]
DEADLINE_RETRY_SECONDS = 1.0
DEADLINE_MAX_RETRY_SECONDS = 30.0


class TrialEventConflictError(ValueError):
    """Raised when a command id is reused for different input."""


class TrialEventInProgressError(RuntimeError):
    """Raised for a concurrent duplicate while the first handler still runs."""


class TrialPreparationRequiredError(RuntimeError):
    """A start command has no durable preparation or explicit local override."""


class TrialEventService:
    VERSION = 2

    def __init__(
        self,
        data_dir: Path,
        *,
        clock: Callable[[], float] = time.time,
        timer_factory: TimerFactory = threading.Timer,
        scheduling_enabled: bool = True,
    ) -> None:
        self.path = Path(data_dir) / "runtime" / "trial-events.json"
        self._clock = clock
        self._timer_factory = timer_factory
        self._scheduling_enabled = bool(scheduling_enabled)
        self._lock = threading.RLock()
        self.journals = SessionJournalStore(Path(data_dir), clock=clock)
        # Start and stop for the same stimulus must never run concurrently.
        # In particular, a deadline can expire while a slow plugin start is
        # still executing. Without this lock the automatic stop may finish
        # first and the late start can leave hardware recording indefinitely.
        self._lifecycle_locks: dict[str, threading.RLock] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._state: dict[str, Any] = {
            "version": self.VERSION,
            "events": {},
            "preparations": {},
            "deadlines": {},
            "prepare_overrides": {},
            "prepare_cancellations": [],
        }
        self._load()

    def execute(
        self,
        event_id: str | None,
        kind: str,
        payload: dict[str, Any],
        handler: JournalHandler,
    ) -> dict[str, Any]:
        """Run ``handler`` once, serializing start/stop for one stimulus."""

        normalized_kind = str(kind or "event").strip() or "event"
        stimulus_id = str(payload.get("stimulus_id") or "").strip()
        if normalized_kind not in {"trial_start", "trial_stop"} or not stimulus_id:
            return self._execute_once(event_id, normalized_kind, payload, handler)

        with self._lock:
            lifecycle_lock = self._lifecycle_locks.setdefault(stimulus_id, threading.RLock())
        with lifecycle_lock:
            return self._execute_once(event_id, normalized_kind, payload, handler)

    def _execute_once(
        self,
        event_id: str | None,
        kind: str,
        payload: dict[str, Any],
        handler: JournalHandler,
    ) -> dict[str, Any]:
        """Run ``handler`` once for one stable event id and persist its response."""

        normalized_id = _event_id(event_id)
        normalized_kind = str(kind or "event").strip() or "event"
        payload_copy = deepcopy(payload)
        payload_copy["event_id"] = normalized_id
        fingerprint = _fingerprint(normalized_kind, payload_copy)

        with self._lock:
            previous = self._state["events"].get(normalized_id)
            if previous:
                self._assert_same_event(previous, normalized_kind, fingerprint)
                if previous.get("status") == "done":
                    return {
                        **deepcopy(previous.get("response") or {}),
                        "event_id": normalized_id,
                        "duplicate": True,
                    }
                if previous.get("status") == "processing":
                    raise TrialEventInProgressError(
                        f"Trial event {normalized_id!r} is already being processed."
                    )

            # ``authorize_start`` runs before the potentially slow runtime
            # readiness check in the Flask route. Re-check while holding the
            # per-stimulus lifecycle lock so a deadline that fired in between
            # cannot stop first and then be followed by a late start.
            stimulus_id = str(payload_copy.get("stimulus_id") or "").strip()
            if normalized_kind == "trial_start" and stimulus_id:
                deadline_record = self._state["deadlines"].get(stimulus_id)
                deadline_epoch_ms = float((deadline_record or {}).get("deadline_epoch_ms") or 0)
                if (
                    not isinstance(deadline_record, dict)
                    or deadline_record.get("status") != "armed"
                    or deadline_epoch_ms <= self._clock() * 1000.0
                ):
                    raise TrialPreparationRequiredError(
                        f"Stimulus id {stimulus_id!r} has no active server stop deadline."
                    )

            now = self._clock()
            previous_components = deepcopy((previous or {}).get("component_outcomes") or {})
            self._state["events"][normalized_id] = {
                "event_id": normalized_id,
                "kind": normalized_kind,
                "fingerprint": fingerprint,
                "status": "processing",
                "attempts": int((previous or {}).get("attempts") or 0) + 1,
                "source_epoch_ms": payload_copy.get("source_epoch_ms", payload_copy.get("client_trigger_epoch_ms")),
                "visual_onset_epoch_ms": payload_copy.get("visual_onset_epoch_ms"),
                "onset_uncertainty_ms": payload_copy.get("onset_uncertainty_ms"),
                "server_received_epoch_ms": payload_copy.get("server_received_epoch_ms"),
                "session_id": payload_copy.get("session_id"),
                "client_id": payload_copy.get("client_id"),
                "started_at_epoch_ms": round(now * 1000.0, 3),
                "component_outcomes": previous_components,
            }
            self._persist_locked(
                journal_event=f"{normalized_kind}_processing",
                session_ids=(payload_copy.get("session_id"),),
            )

        if previous_components:
            # Private retry state lets the trial dispatcher replay only failed
            # components. A successful LSL marker or plugin callback must not
            # be emitted a second time merely because another plugin failed.
            payload_copy["_trial_component_outcomes"] = previous_components

        try:
            response = dict(handler(payload_copy) or {})
        except Exception as error:
            with self._lock:
                record = self._state["events"][normalized_id]
                component_outcomes = getattr(error, "outcomes", None)
                record.update(
                    {
                        "status": "failed",
                        "last_error": str(error),
                        "finished_at_epoch_ms": round(self._clock() * 1000.0, 3),
                    }
                )
                if isinstance(component_outcomes, dict):
                    record["component_outcomes"] = deepcopy(component_outcomes)
                partial_response = getattr(error, "response", None)
                if isinstance(partial_response, dict):
                    record["partial_response"] = deepcopy(partial_response)
                self._persist_locked(
                    journal_event=f"{normalized_kind}_failed",
                    session_ids=(payload_copy.get("session_id"),),
                )
            raise

        with self._lock:
            record = self._state["events"][normalized_id]
            record.update(
                {
                    "status": "done",
                    "response": deepcopy(response),
                    "component_outcomes": deepcopy(response.get("dispatch") or {}),
                    "last_error": "",
                    "finished_at_epoch_ms": round(self._clock() * 1000.0, 3),
                }
            )
            self._persist_locked(
                journal_event=f"{normalized_kind}_completed",
                session_ids=(payload_copy.get("session_id"),),
            )

        return {**response, "event_id": normalized_id, "duplicate": False}

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the planned onset/deadline before the visual phase begins."""
        stimulus_id = _required_identifier(payload.get("stimulus_id"), "stimulus_id")
        event_id = _required_identifier(payload.get("event_id"), "event_id")
        stop_event_id = _required_identifier(payload.get("stop_event_id"), "stop_event_id")
        deadline = _required_deadline(payload.get("planned_deadline_epoch_ms"))
        if deadline <= self._clock() * 1000.0:
            raise ValueError("planned_deadline_epoch_ms has already elapsed.")
        prepared = {
            "stimulus_id": stimulus_id,
            "event_id": event_id,
            "stop_event_id": stop_event_id,
            "study_id": str(payload.get("study_id") or ""),
            "participant_id": str(payload.get("participant_id") or ""),
            "session_id": str(payload.get("session_id") or ""),
            "client_id": str(payload.get("client_id") or ""),
            "question_index": payload.get("question_index"),
            "planned_start_epoch_ms": payload.get("planned_start_epoch_ms"),
            "planned_deadline_epoch_ms": deadline,
            "server_received_epoch_ms": payload.get("server_received_epoch_ms"),
            "prepared_at_epoch_ms": round(self._clock() * 1000.0, 3),
        }
        with self._lock:
            if self._preparation_cancelled_locked(stimulus_id, event_id):
                raise TrialEventConflictError(
                    f"Stimulus id {stimulus_id!r} was already cancelled and cannot be prepared."
                )
            current = self._state["preparations"].get(stimulus_id)
            if current:
                comparable = {
                    key: value
                    for key, value in prepared.items()
                    if key not in {"prepared_at_epoch_ms", "server_received_epoch_ms"}
                }
                previous = {key: current.get(key) for key in comparable}
                if previous != comparable:
                    raise TrialEventConflictError(
                        f"Stimulus id {stimulus_id!r} was prepared with different timing data."
                    )
                return {**deepcopy(current), "duplicate": True}
            self._state["preparations"][stimulus_id] = prepared
            self._persist_locked(
                journal_event="trial_prepared",
                session_ids=(prepared.get("session_id"),),
            )
        return {**prepared, "duplicate": False}

    def authorize_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Require durable prepare state before any trial-start side effect.

        The only exception is an explicit override created by a loopback-only
        admin route.  This gate is deliberately scoped to trial preparation;
        it neither evaluates nor bypasses study-session/recording readiness.
        """

        stimulus_id = _required_identifier(payload.get("stimulus_id"), "stimulus_id")
        event_id = _required_identifier(payload.get("event_id"), "event_id")
        stop_event_id = _required_identifier(payload.get("stop_event_id"), "stop_event_id")
        deadline = _required_deadline(payload.get("planned_deadline_epoch_ms"))

        with self._lock:
            prior_event = self._state["events"].get(event_id)
            if isinstance(prior_event, dict) and prior_event.get("status") == "done":
                return {"authorized": True, "source": "completed_event", "event_id": event_id}
            if deadline <= self._clock() * 1000.0:
                raise TrialPreparationRequiredError(
                    f"Stimulus id {stimulus_id!r} reached its planned stop deadline before start."
                )

            override = self._state["prepare_overrides"].get(event_id)
            if isinstance(override, dict) and override.get("stimulus_id") == stimulus_id:
                override["last_used_at_epoch_ms"] = round(self._clock() * 1000.0, 3)
                override["use_count"] = int(override.get("use_count") or 0) + 1
                self._persist_locked(
                    journal_event="prepare_override_used",
                    session_ids=(payload.get("session_id"),),
                )
                return {
                    "authorized": True,
                    "source": "local_admin_override",
                    "event_id": event_id,
                    "override": deepcopy(override),
                }

            prepared = self._state["preparations"].get(stimulus_id)
            if isinstance(prepared, dict):
                expected = {
                    "event_id": event_id,
                    "stop_event_id": stop_event_id,
                    "study_id": str(payload.get("study_id") or ""),
                    "participant_id": str(payload.get("participant_id") or ""),
                    "session_id": str(payload.get("session_id") or ""),
                    "client_id": str(payload.get("client_id") or ""),
                    "question_index": payload.get("question_index"),
                    "planned_start_epoch_ms": payload.get("planned_start_epoch_ms"),
                    "planned_deadline_epoch_ms": deadline,
                }
                actual = {key: prepared.get(key) for key in expected}
                if actual != expected:
                    raise TrialEventConflictError(
                        f"Stimulus id {stimulus_id!r} start data differs from its durable preparation."
                    )
                deadline_record = self._state["deadlines"].get(stimulus_id)
                if not isinstance(deadline_record, dict) or deadline_record.get("status") != "armed":
                    raise TrialPreparationRequiredError(
                        f"Stimulus id {stimulus_id!r} has no active server stop deadline."
                    )
                return {
                    "authorized": True,
                    "source": "prepared",
                    "event_id": event_id,
                    "prepared_at_epoch_ms": prepared.get("prepared_at_epoch_ms"),
                }

            raise TrialPreparationRequiredError(
                f"Trial start {event_id!r} was not durably prepared."
            )

    def create_prepare_override(self, event_id: Any, stimulus_id: Any, reason: Any) -> dict[str, Any]:
        """Persist one narrowly scoped manual authorization for trial prepare."""

        normalized_event_id = _required_identifier(event_id, "event_id")
        normalized_stimulus_id = _required_identifier(stimulus_id, "stimulus_id")
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise ValueError("reason is required.")
        if len(normalized_reason) > 500:
            raise ValueError("reason must not exceed 500 characters.")
        record = {
            "event_id": normalized_event_id,
            "stimulus_id": normalized_stimulus_id,
            "reason": normalized_reason,
            "scope": "trial_prepare_only",
            "created_at_epoch_ms": round(self._clock() * 1000.0, 3),
            "use_count": 0,
        }
        with self._lock:
            previous = self._state["prepare_overrides"].get(normalized_event_id)
            if previous:
                comparable = {
                    key: record[key]
                    for key in ("event_id", "stimulus_id", "reason", "scope")
                }
                if {key: previous.get(key) for key in comparable} != comparable:
                    raise TrialEventConflictError(
                        f"Trial prepare override {normalized_event_id!r} already has different data."
                    )
                return {**deepcopy(previous), "duplicate": True}
            self._state["prepare_overrides"][normalized_event_id] = record
            try:
                self._persist_locked(
                    journal_event="prepare_override_created",
                    session_ids=(
                        self._resolved_session_id_locked(
                            record=record,
                            event_id=normalized_event_id,
                            stimulus_id=normalized_stimulus_id,
                        ),
                    ),
                )
            except Exception:
                # Authorization must fail closed if its QC journal cannot be
                # durably appended, including within this running process.
                self._state["prepare_overrides"].pop(normalized_event_id, None)
                raise
            return {**deepcopy(record), "duplicate": False}

    def get_prepare_override(self, event_id: Any) -> dict[str, Any] | None:
        normalized_event_id = _required_identifier(event_id, "event_id")
        with self._lock:
            record = self._state["prepare_overrides"].get(normalized_event_id)
            return deepcopy(record) if isinstance(record, dict) else None

    def cancel_preparation(
        self,
        event_id: Any,
        stimulus_id: Any,
        reason: Any,
    ) -> dict[str, Any]:
        """Journal an operator/tablet skip before disarming its deadline.

        The cancellation list is append-only for QC. The first persistence is
        deliberately completed while holding the timer lock and before the
        deadline is touched. On restart, ``_load`` reconciles any journalled
        cancellation whose second write was interrupted before timers resume.
        """

        normalized_event_id = _required_identifier(event_id, "event_id")
        normalized_stimulus_id = _required_identifier(stimulus_id, "stimulus_id")
        normalized_reason = str(reason or "").strip()
        if normalized_reason not in {"tablet_skip", "abort"}:
            raise ValueError("reason must be tablet_skip or abort.")

        with self._lock:
            started_event = self._state["events"].get(normalized_event_id)
            if isinstance(started_event, dict):
                # Once execute() has created its processing record, at least
                # one start side effect may already have happened. Silently
                # removing the backup stop could then leave hardware running.
                raise TrialEventConflictError(
                    f"Trial start {normalized_event_id!r} already began; stop it instead of cancelling prepare."
                )
            preparation = self._state["preparations"].get(normalized_stimulus_id)
            if isinstance(preparation, dict) and preparation.get("event_id") != normalized_event_id:
                raise TrialEventConflictError(
                    f"Stimulus id {normalized_stimulus_id!r} belongs to a different start event."
                )
            existing = next(
                (
                    item
                    for item in self._state["prepare_cancellations"]
                    if isinstance(item, dict) and item.get("event_id") == normalized_event_id
                ),
                None,
            )
            if existing:
                if (
                    existing.get("stimulus_id") != normalized_stimulus_id
                    or existing.get("reason") != normalized_reason
                ):
                    raise TrialEventConflictError(
                        f"Preparation cancellation {normalized_event_id!r} already has different data."
                    )
                cancellation = existing
                duplicate = True
            else:
                cancellation = {
                    "event_id": normalized_event_id,
                    "stimulus_id": normalized_stimulus_id,
                    "reason": normalized_reason,
                    "scope": "trial_prepare",
                    "cancelled_at_epoch_ms": round(self._clock() * 1000.0, 3),
                }
                self._state["prepare_cancellations"].append(cancellation)
                # QC intent is durable before the timer is cancelled.
                self._persist_locked(
                    journal_event="trial_prepare_cancelled",
                    session_ids=(
                        self._resolved_session_id_locked(
                            record=preparation,
                            event_id=normalized_event_id,
                            stimulus_id=normalized_stimulus_id,
                        ),
                    ),
                )
                duplicate = False

            deadline_transitioned = self._cancel_deadline_locked(normalized_stimulus_id)
            if deadline_transitioned:
                self._persist_locked(
                    journal_event="trial_prepare_deadline_cancelled",
                    session_ids=(
                        self._resolved_session_id_locked(
                            record=preparation,
                            event_id=normalized_event_id,
                            stimulus_id=normalized_stimulus_id,
                        ),
                    ),
                )
            deadline = self._state["deadlines"].get(normalized_stimulus_id)
            return {
                "cancellation": deepcopy(cancellation),
                "deadline_cancelled": bool(
                    isinstance(deadline, dict) and deadline.get("status") == "cancelled"
                ),
                "deadline_transitioned": deadline_transitioned,
                "duplicate": duplicate,
            }

    def arm_deadline(
        self,
        stimulus_id: str,
        deadline_epoch_ms: float | int | None,
        stop_payload: dict[str, Any],
        handler: JournalHandler,
    ) -> dict[str, Any] | None:
        """Persist and schedule an automatic idempotent stop command."""
        normalized_stimulus_id = str(stimulus_id or "").strip()
        if not normalized_stimulus_id or deadline_epoch_ms is None:
            return None
        deadline = float(deadline_epoch_ms)
        if deadline <= 0:
            return None

        event_id = str(stop_payload.get("event_id") or f"{normalized_stimulus_id}:deadline-stop")
        payload_copy = deepcopy(stop_payload)
        payload_copy.update(
            {
                "event_id": event_id,
                "stimulus_id": normalized_stimulus_id,
                "phase": "stimulus_active_stop",
                "marker_event": "stimulus_active_stop",
                "automatic_deadline": True,
                "client_trigger_epoch_ms": deadline,
                "source_epoch_ms": deadline,
            }
        )
        # A prepare request's receive time is not the receive/fire time of the
        # later automatic stop.  It must never leak into that stop marker.
        payload_copy.pop("server_received_epoch_ms", None)
        payload_copy.pop("server_received_at", None)

        with self._lock:
            if self._preparation_cancelled_locked(normalized_stimulus_id):
                raise TrialEventConflictError(
                    f"Stimulus id {normalized_stimulus_id!r} was cancelled and cannot arm a deadline."
                )
            previous = self._state["deadlines"].get(normalized_stimulus_id)
            if previous:
                if float(previous.get("deadline_epoch_ms") or 0) != deadline:
                    raise TrialEventConflictError(
                        f"Stimulus id {normalized_stimulus_id!r} has a different deadline."
                    )
                if previous.get("status") in {"armed", "firing", "fired", "cancelled"}:
                    return deepcopy(previous)

            record = {
                "stimulus_id": normalized_stimulus_id,
                "event_id": event_id,
                "deadline_epoch_ms": deadline,
                "status": "armed",
                "stop_payload": payload_copy,
                "armed_at_epoch_ms": round(self._clock() * 1000.0, 3),
            }
            self._state["deadlines"][normalized_stimulus_id] = record
            self._persist_locked(
                journal_event="trial_deadline_armed",
                session_ids=(payload_copy.get("session_id"),),
            )
            self._schedule_locked(normalized_stimulus_id, handler)
            return deepcopy(record)

    def cancel_deadline(self, stimulus_id: str) -> bool:
        normalized_stimulus_id = str(stimulus_id or "").strip()
        if not normalized_stimulus_id:
            return False
        with self._lock:
            cancelled = self._cancel_deadline_locked(normalized_stimulus_id)
            if cancelled:
                deadline_record = self._state["deadlines"].get(normalized_stimulus_id)
                self._persist_locked(
                    journal_event="trial_deadline_cancelled",
                    session_ids=(
                        self._resolved_session_id_locked(
                            record=deadline_record,
                            stimulus_id=normalized_stimulus_id,
                        ),
                    ),
                )
            return cancelled

    def resume_pending(self, handler: JournalHandler) -> int:
        """Re-arm persisted deadlines after a Flask restart."""
        with self._lock:
            pending = [
                stimulus_id
                for stimulus_id, record in self._state["deadlines"].items()
                if record.get("status") in {"armed", "firing", "failed"}
            ]
            for stimulus_id in pending:
                self._state["deadlines"][stimulus_id]["status"] = "armed"
                self._schedule_locked(stimulus_id, handler)
            if pending:
                self._persist_locked(
                    journal_event="trial_deadlines_resumed",
                    session_ids=(
                        self._resolved_session_id_locked(
                            record=self._state["deadlines"].get(stimulus_id),
                            stimulus_id=stimulus_id,
                        )
                        for stimulus_id in pending
                    ),
                )
            return len(pending)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def close(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    def _schedule_locked(
        self,
        stimulus_id: str,
        handler: JournalHandler,
        *,
        retry_delay: float | None = None,
    ) -> None:
        if not self._scheduling_enabled or stimulus_id in self._timers:
            return
        record = self._state["deadlines"].get(stimulus_id) or {}
        deadline = float(record.get("deadline_epoch_ms") or 0) / 1000.0
        delay = (
            max(0.0, float(retry_delay))
            if retry_delay is not None
            else max(0.0, deadline - self._clock())
        )
        timer = self._timer_factory(delay, lambda: self._fire_deadline(stimulus_id, handler))
        timer.daemon = True
        self._timers[stimulus_id] = timer
        timer.start()

    def _fire_deadline(self, stimulus_id: str, handler: JournalHandler) -> None:
        with self._lock:
            self._timers.pop(stimulus_id, None)
            record = self._state["deadlines"].get(stimulus_id)
            if not record or record.get("status") != "armed":
                return
            record["status"] = "firing"
            self._persist_locked(
                journal_event="trial_deadline_firing",
                session_ids=(
                    self._resolved_session_id_locked(record=record, stimulus_id=stimulus_id),
                ),
            )
            payload = deepcopy(record.get("stop_payload") or {})
            event_id = str(record.get("event_id") or "")
            payload["server_received_epoch_ms"] = round(self._clock() * 1000.0, 3)

        try:
            # The browser stop and this deadline stop intentionally share one
            # event id. Whichever arrives first performs the side effect; the
            # other receives the persisted response as an idempotent duplicate.
            response = self.execute(event_id, "trial_stop", payload, handler)
        except Exception as error:
            with self._lock:
                record = self._state["deadlines"].get(stimulus_id) or {}
                # Keep safety armed. A transient plugin failure must not turn a
                # server stop deadline into a one-shot best effort.
                record["status"] = "armed"
                record["last_error"] = str(error)
                record["failure_count"] = int(record.get("failure_count") or 0) + 1
                record["last_failed_at_epoch_ms"] = round(self._clock() * 1000.0, 3)
                self._persist_locked(
                    journal_event="trial_deadline_retry_armed",
                    session_ids=(
                        self._resolved_session_id_locked(record=record, stimulus_id=stimulus_id),
                    ),
                )
                retry_delay = min(
                    DEADLINE_MAX_RETRY_SECONDS,
                    DEADLINE_RETRY_SECONDS * (2 ** min(record["failure_count"] - 1, 5)),
                )
                self._schedule_locked(
                    stimulus_id,
                    handler,
                    retry_delay=retry_delay,
                )
            return

        with self._lock:
            record = self._state["deadlines"].get(stimulus_id) or {}
            record["status"] = "fired"
            record["response"] = response
            record["fired_at_epoch_ms"] = round(self._clock() * 1000.0, 3)
            self._persist_locked(
                journal_event="trial_deadline_fired",
                session_ids=(
                    self._resolved_session_id_locked(record=record, stimulus_id=stimulus_id),
                ),
            )

    def _assert_same_event(self, previous: dict[str, Any], kind: str, fingerprint: str) -> None:
        if previous.get("kind") != kind or previous.get("fingerprint") != fingerprint:
            raise TrialEventConflictError(
                f"Trial event id {previous.get('event_id')!r} was reused for different input."
            )

    def _cancel_deadline_locked(self, stimulus_id: str) -> bool:
        record = self._state["deadlines"].get(stimulus_id)
        if not record or record.get("status") in {"fired", "cancelled"}:
            return False
        record["status"] = "cancelled"
        record["cancelled_at_epoch_ms"] = round(self._clock() * 1000.0, 3)
        timer = self._timers.pop(stimulus_id, None)
        if timer is not None:
            timer.cancel()
        return True

    def _preparation_cancelled_locked(
        self,
        stimulus_id: str,
        event_id: str | None = None,
    ) -> bool:
        """Return whether append-only QC already cancelled this identity."""

        return any(
            isinstance(item, dict)
            and (
                item.get("stimulus_id") == stimulus_id
                or (event_id is not None and item.get("event_id") == event_id)
            )
            for item in self._state["prepare_cancellations"]
        )

    def _load(self) -> None:
        raw: dict[str, Any] = {}
        projection_error: Exception | None = None
        if self.path.is_file():
            try:
                candidate = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(candidate, dict):
                    raise ValueError("trial-event projection is not a JSON object")
                raw = candidate
            except (OSError, ValueError) as error:
                projection_error = error

        for key in ("events", "preparations", "deadlines", "prepare_overrides"):
            value = raw.get(key)
            if isinstance(value, dict):
                self._state[key] = deepcopy(value)
        cancellations = raw.get("prepare_cancellations")
        if isinstance(cancellations, list):
            self._state["prepare_cancellations"] = [
                deepcopy(item) for item in cancellations if isinstance(item, dict)
            ]

        journal_records = self.journals.latest_snapshots("trial")
        if projection_error is not None and not journal_records:
            raise SessionJournalCorruptionError(
                f"Could not recover {self.path.name}: {projection_error}"
            ) from projection_error
        recovered_from_journal = False
        for record in sorted(journal_records.values(), key=_journal_record_order):
            snapshot = record.get("snapshot")
            if not isinstance(snapshot, dict):
                raise SessionJournalCorruptionError(
                    f"Trial journal {record.get('record_id')} has no state snapshot."
                )
            recovered_from_journal = self._merge_journal_snapshot_locked(snapshot) or recovered_from_journal

        recovered = False
        for cancellation in self._state["prepare_cancellations"]:
            stimulus_id = str(cancellation.get("stimulus_id") or "")
            record = self._state["deadlines"].get(stimulus_id)
            if isinstance(record, dict) and record.get("status") not in {"fired", "cancelled"}:
                record["status"] = "cancelled"
                record["cancelled_at_epoch_ms"] = cancellation.get("cancelled_at_epoch_ms")
                recovered = True
        for record in self._state["events"].values():
            if isinstance(record, dict) and record.get("status") == "processing":
                # The side effect may or may not have happened before the
                # process died. Allow a same-id reconciliation attempt instead
                # of leaving the event permanently locked. Downstream markers
                # retain the event id so a physical duplicate is detectable as
                # a QC warning.
                record["status"] = "reconcile_required"
                record["last_error"] = "Server restarted while this event was processing."
                recovered = True
        has_state = any(
            bool(self._state.get(key))
            for key in (
                "events",
                "preparations",
                "deadlines",
                "prepare_overrides",
                "prepare_cancellations",
            )
        )
        migrated_legacy_projection = has_state and not journal_records
        if recovered or recovered_from_journal or migrated_legacy_projection:
            self._persist_locked(
                journal_event=(
                    "legacy_projection_migrated"
                    if migrated_legacy_projection
                    else "trial_state_recovered"
                )
            )

    def _persist_locked(
        self,
        *,
        journal_event: str = "trial_state_updated",
        session_ids: Iterable[Any] | None = None,
    ) -> None:
        journal_session_ids = self._journal_session_ids_locked(session_ids)
        for session_id in sorted(journal_session_ids):
            self.journals.append(
                "trial",
                session_id,
                journal_event,
                self._journal_snapshot_locked(session_id),
            )
        atomic_write_json(self.path, self._state)

    def _journal_session_ids_locked(
        self,
        requested: Iterable[Any] | None,
    ) -> set[str]:
        if requested is not None:
            normalized = {
                str(value or "").strip() or UNBOUND_SESSION_ID
                for value in requested
            }
            return normalized or {UNBOUND_SESSION_ID}

        result: set[str] = set()
        for record in self._state["events"].values():
            result.add(self._resolved_session_id_locked(record=record))
        for record in self._state["preparations"].values():
            result.add(self._resolved_session_id_locked(record=record))
        for stimulus_id, record in self._state["deadlines"].items():
            result.add(
                self._resolved_session_id_locked(
                    record=record,
                    event_id=record.get("event_id") if isinstance(record, dict) else None,
                    stimulus_id=stimulus_id,
                )
            )
        for event_id, record in self._state["prepare_overrides"].items():
            result.add(
                self._resolved_session_id_locked(
                    record=record,
                    event_id=event_id,
                    stimulus_id=record.get("stimulus_id") if isinstance(record, dict) else None,
                )
            )
        for record in self._state["prepare_cancellations"]:
            if isinstance(record, dict):
                result.add(
                    self._resolved_session_id_locked(
                        record=record,
                        event_id=record.get("event_id"),
                        stimulus_id=record.get("stimulus_id"),
                    )
                )
        return result

    def _resolved_session_id_locked(
        self,
        *,
        record: Any = None,
        event_id: Any = None,
        stimulus_id: Any = None,
    ) -> str:
        if isinstance(record, dict):
            direct = str(record.get("session_id") or "").strip()
            if direct:
                return direct
            stop_payload = record.get("stop_payload")
            if isinstance(stop_payload, dict):
                direct = str(stop_payload.get("session_id") or "").strip()
                if direct:
                    return direct

        normalized_event_id = str(event_id or "").strip()
        if normalized_event_id:
            event_record = self._state["events"].get(normalized_event_id)
            if isinstance(event_record, dict):
                direct = str(event_record.get("session_id") or "").strip()
                if direct:
                    return direct
            for preparation in self._state["preparations"].values():
                if isinstance(preparation, dict) and preparation.get("event_id") == normalized_event_id:
                    direct = str(preparation.get("session_id") or "").strip()
                    if direct:
                        return direct

        normalized_stimulus_id = str(stimulus_id or "").strip()
        if normalized_stimulus_id:
            preparation = self._state["preparations"].get(normalized_stimulus_id)
            if isinstance(preparation, dict):
                direct = str(preparation.get("session_id") or "").strip()
                if direct:
                    return direct
        return UNBOUND_SESSION_ID

    def _journal_snapshot_locked(self, session_id: str) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip() or UNBOUND_SESSION_ID

        def belongs(record: Any, *, event_id: Any = None, stimulus_id: Any = None) -> bool:
            return self._resolved_session_id_locked(
                record=record,
                event_id=event_id,
                stimulus_id=stimulus_id,
            ) == normalized_session_id

        return {
            "version": self.VERSION,
            "events": {
                key: deepcopy(value)
                for key, value in self._state["events"].items()
                if belongs(value, event_id=key, stimulus_id=(value or {}).get("stimulus_id"))
            },
            "preparations": {
                key: deepcopy(value)
                for key, value in self._state["preparations"].items()
                if belongs(value, event_id=(value or {}).get("event_id"), stimulus_id=key)
            },
            "deadlines": {
                key: deepcopy(value)
                for key, value in self._state["deadlines"].items()
                if belongs(value, event_id=(value or {}).get("event_id"), stimulus_id=key)
            },
            "prepare_overrides": {
                key: deepcopy(value)
                for key, value in self._state["prepare_overrides"].items()
                if belongs(value, event_id=key, stimulus_id=(value or {}).get("stimulus_id"))
            },
            "prepare_cancellations": [
                deepcopy(value)
                for value in self._state["prepare_cancellations"]
                if belongs(
                    value,
                    event_id=(value or {}).get("event_id"),
                    stimulus_id=(value or {}).get("stimulus_id"),
                )
            ],
        }

    def _merge_journal_snapshot_locked(self, snapshot: dict[str, Any]) -> bool:
        changed = False
        for key in ("events", "preparations", "deadlines", "prepare_overrides"):
            values = snapshot.get(key)
            if not isinstance(values, dict):
                continue
            for identity, value in values.items():
                if isinstance(value, dict) and self._state[key].get(identity) != value:
                    self._state[key][identity] = deepcopy(value)
                    changed = True
        cancellations = snapshot.get("prepare_cancellations")
        if isinstance(cancellations, list):
            by_event = {
                str(item.get("event_id") or ""): index
                for index, item in enumerate(self._state["prepare_cancellations"])
                if isinstance(item, dict)
            }
            for item in cancellations:
                if not isinstance(item, dict):
                    continue
                event_id = str(item.get("event_id") or "")
                prior_index = by_event.get(event_id)
                if prior_index is None:
                    by_event[event_id] = len(self._state["prepare_cancellations"])
                    self._state["prepare_cancellations"].append(deepcopy(item))
                    changed = True
                elif self._state["prepare_cancellations"][prior_index] != item:
                    self._state["prepare_cancellations"][prior_index] = deepcopy(item)
                    changed = True
        return changed


def _event_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or str(uuid.uuid4())


def _required_identifier(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    if len(normalized) > 200:
        raise ValueError(f"{field_name} must not exceed 200 characters.")
    return normalized


def _required_deadline(value: Any) -> float:
    try:
        deadline = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("planned_deadline_epoch_ms is required.") from error
    if not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("planned_deadline_epoch_ms must be greater than zero.")
    return deadline


def _fingerprint(kind: str, payload: dict[str, Any]) -> str:
    fingerprint_kind = kind
    # Transport-observation fields are intentionally different on a retry and
    # therefore cannot define command identity.
    fingerprint_payload = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "server_received_epoch_ms",
            "server_received_at",
            "_trial_component_outcomes",
        }
    }
    if kind == "trial_stop":
        # Callback observation timestamps differ between the server timer and a
        # throttled tablet callback. They describe transport quality, not a
        # different command. Stable session/stimulus identity and policy fields
        # still protect against accidental event-id reuse.
        fingerprint_payload = {
            key: payload.get(key)
            for key in (
                "event_id",
                "stimulus_id",
                "study_id",
                "participant_id",
                "question_index",
                "question_type",
                "planned_deadline_epoch_ms",
                "plugin_actions",
            )
        }
    encoded = json.dumps(
        {"kind": fingerprint_kind, "payload": fingerprint_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
