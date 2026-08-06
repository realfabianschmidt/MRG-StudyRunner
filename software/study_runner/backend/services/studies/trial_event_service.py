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

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
from typing import Any

from ..shared.atomic_io import atomic_write_json


JournalHandler = Callable[[dict[str, Any]], dict[str, Any] | None]
TimerFactory = Callable[[float, Callable[[], None]], threading.Timer]


class TrialEventConflictError(ValueError):
    """Raised when a command id is reused for different input."""


class TrialEventInProgressError(RuntimeError):
    """Raised for a concurrent duplicate while the first handler still runs."""


class TrialEventService:
    VERSION = 1

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
        self._timers: dict[str, threading.Timer] = {}
        self._state: dict[str, Any] = {
            "version": self.VERSION,
            "events": {},
            "preparations": {},
            "deadlines": {},
        }
        self._load()

    def execute(
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

            now = self._clock()
            self._state["events"][normalized_id] = {
                "event_id": normalized_id,
                "kind": normalized_kind,
                "fingerprint": fingerprint,
                "status": "processing",
                "attempts": int((previous or {}).get("attempts") or 0) + 1,
                "source_epoch_ms": payload_copy.get("client_trigger_epoch_ms"),
                "started_at_epoch_ms": round(now * 1000.0, 3),
            }
            self._persist_locked()

        try:
            response = dict(handler(payload_copy) or {})
        except Exception as error:
            with self._lock:
                record = self._state["events"][normalized_id]
                record.update(
                    {
                        "status": "failed",
                        "last_error": str(error),
                        "finished_at_epoch_ms": round(self._clock() * 1000.0, 3),
                    }
                )
                self._persist_locked()
            raise

        with self._lock:
            record = self._state["events"][normalized_id]
            record.update(
                {
                    "status": "done",
                    "response": deepcopy(response),
                    "last_error": "",
                    "finished_at_epoch_ms": round(self._clock() * 1000.0, 3),
                }
            )
            self._persist_locked()

        return {**response, "event_id": normalized_id, "duplicate": False}

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the planned onset/deadline before the visual phase begins."""
        stimulus_id = str(payload.get("stimulus_id") or uuid.uuid4()).strip()
        if not stimulus_id:
            stimulus_id = str(uuid.uuid4())
        prepared = {
            "stimulus_id": stimulus_id,
            "study_id": str(payload.get("study_id") or ""),
            "participant_id": str(payload.get("participant_id") or ""),
            "question_index": payload.get("question_index"),
            "planned_start_epoch_ms": payload.get("planned_start_epoch_ms"),
            "planned_deadline_epoch_ms": payload.get("planned_deadline_epoch_ms"),
            "prepared_at_epoch_ms": round(self._clock() * 1000.0, 3),
        }
        with self._lock:
            current = self._state["preparations"].get(stimulus_id)
            if current:
                comparable = {key: value for key, value in prepared.items() if key != "prepared_at_epoch_ms"}
                previous = {key: current.get(key) for key in comparable}
                if previous != comparable:
                    raise TrialEventConflictError(
                        f"Stimulus id {stimulus_id!r} was prepared with different timing data."
                    )
                return {**deepcopy(current), "duplicate": True}
            self._state["preparations"][stimulus_id] = prepared
            self._persist_locked()
        return {**prepared, "duplicate": False}

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
            }
        )

        with self._lock:
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
            self._persist_locked()
            self._schedule_locked(normalized_stimulus_id, handler)
            return deepcopy(record)

    def cancel_deadline(self, stimulus_id: str) -> bool:
        normalized_stimulus_id = str(stimulus_id or "").strip()
        if not normalized_stimulus_id:
            return False
        with self._lock:
            record = self._state["deadlines"].get(normalized_stimulus_id)
            if not record or record.get("status") in {"fired", "cancelled"}:
                return False
            record["status"] = "cancelled"
            record["cancelled_at_epoch_ms"] = round(self._clock() * 1000.0, 3)
            timer = self._timers.pop(normalized_stimulus_id, None)
            if timer is not None:
                timer.cancel()
            self._persist_locked()
            return True

    def resume_pending(self, handler: JournalHandler) -> int:
        """Re-arm persisted deadlines after a Flask restart."""
        with self._lock:
            pending = [
                stimulus_id
                for stimulus_id, record in self._state["deadlines"].items()
                if record.get("status") in {"armed", "firing"}
            ]
            for stimulus_id in pending:
                self._state["deadlines"][stimulus_id]["status"] = "armed"
                self._schedule_locked(stimulus_id, handler)
            if pending:
                self._persist_locked()
            return len(pending)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def close(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    def _schedule_locked(self, stimulus_id: str, handler: JournalHandler) -> None:
        if not self._scheduling_enabled or stimulus_id in self._timers:
            return
        record = self._state["deadlines"].get(stimulus_id) or {}
        deadline = float(record.get("deadline_epoch_ms") or 0) / 1000.0
        delay = max(0.0, deadline - self._clock())
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
            self._persist_locked()
            payload = deepcopy(record.get("stop_payload") or {})
            event_id = str(record.get("event_id") or "")

        try:
            # The browser stop and this deadline stop intentionally share one
            # event id. Whichever arrives first performs the side effect; the
            # other receives the persisted response as an idempotent duplicate.
            response = self.execute(event_id, "trial_stop", payload, handler)
        except Exception as error:
            with self._lock:
                record = self._state["deadlines"].get(stimulus_id) or {}
                record["status"] = "failed"
                record["last_error"] = str(error)
                record["fired_at_epoch_ms"] = round(self._clock() * 1000.0, 3)
                self._persist_locked()
            return

        with self._lock:
            record = self._state["deadlines"].get(stimulus_id) or {}
            record["status"] = "fired"
            record["response"] = response
            record["fired_at_epoch_ms"] = round(self._clock() * 1000.0, 3)
            self._persist_locked()

    def _assert_same_event(self, previous: dict[str, Any], kind: str, fingerprint: str) -> None:
        if previous.get("kind") != kind or previous.get("fingerprint") != fingerprint:
            raise TrialEventConflictError(
                f"Trial event id {previous.get('event_id')!r} was reused for different input."
            )

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"[TRIAL-EVENTS] Could not read {self.path.name}: {error}")
            return
        if not isinstance(raw, dict):
            return
        for key in ("events", "preparations", "deadlines"):
            value = raw.get(key)
            if isinstance(value, dict):
                self._state[key] = value
        recovered = False
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
        if recovered:
            self._persist_locked()

    def _persist_locked(self) -> None:
        atomic_write_json(self.path, self._state)


def _event_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    return normalized or str(uuid.uuid4())


def _fingerprint(kind: str, payload: dict[str, Any]) -> str:
    fingerprint_kind = kind
    fingerprint_payload = payload
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
