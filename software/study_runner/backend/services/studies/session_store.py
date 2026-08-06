"""Persistent registry of active study sessions.

``STUDY_SESSIONS`` used to live only in ``current_app.config``, so a server
restart silently forgot every session and the tablet's resume call returned
404. Every mutation here is atomically persisted to
``DATA_DIR/runtime/study_sessions.json``, and the registry rehydrates from
that file on the next boot. A session whose last activity is older than
``stale_after_seconds`` comes back marked "stale" instead of "active" so a
genuinely abandoned session cannot be resumed, while one interrupted moments
before a restart can be.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from study_runner.shared.atomic_io import atomic_write_json

STALE_AFTER_SECONDS = 12 * 60 * 60
MAX_EVENTS_PER_SESSION = 50
INTERRUPTION_EVENTS = {"client_reload_or_leave", "pagehide", "beforeunload"}


class SessionStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        clock: Callable[[], float] = time.time,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "runtime" / "study_sessions.json"
        self._clock = clock
        self._stale_after = stale_after_seconds
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"[SESSIONS] Could not read {self.path.name}: {error}")
            return
        if not isinstance(raw, dict):
            return
        now = self._clock()
        for session_id, session in raw.items():
            if not isinstance(session, dict):
                continue
            if session.get("status") == "active" and now - float(session.get("last_seen") or 0) > self._stale_after:
                session["status"] = "stale"
            self._sessions[str(session_id)] = session

    def _persist(self) -> None:
        atomic_write_json(self.path, self._sessions)

    def find_active(self, study_id: str, participant_id: str, client_id: str = "") -> dict[str, Any] | None:
        with self._lock:
            session = self._find_active_locked(study_id, participant_id, client_id)
            return dict(session) if session is not None else None

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(session_id)
            return dict(session) if session is not None else None

    def start_or_reuse(self, payload: dict[str, Any]) -> dict[str, Any]:
        study_id = str(payload.get("study_id") or "").strip()
        participant_id = str(payload.get("participant_id") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        with self._lock:
            existing = self._find_active_locked(study_id, participant_id, client_id)
            now = self._clock()
            if existing is not None:
                existing["last_seen"] = now
                existing["last_seen_at"] = _format_server_time(now)
                self._persist()
                return {**existing, "reused": True}

            session_id = str(payload.get("session_id") or f"study-session-{uuid.uuid4()}").strip()
            session = {
                "session_id": session_id,
                "client_id": client_id,
                "study_id": study_id,
                "participant_id": participant_id,
                "current_index": payload.get("current_index"),
                "current_type": payload.get("current_type"),
                "status": "active",
                "started_at": _format_server_time(now),
                "started_at_epoch": now,
                "last_seen": now,
                "last_seen_at": _format_server_time(now),
                "events": [],
            }
            self._sessions[session_id] = session
            self._persist()
            return {**session, "reused": False}

    def resume(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        session_id = str(payload.get("session_id") or "").strip()
        study_id = str(payload.get("study_id") or "").strip()
        participant_id = str(payload.get("participant_id") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        with self._lock:
            session = self._sessions.get(session_id) if session_id else None
            if session is not None:
                if session.get("status") != "active" or not _session_matches_payload(
                    session,
                    study_id=study_id,
                    participant_id=participant_id,
                    client_id=client_id,
                ):
                    return None
            else:
                session = self._find_active_locked(study_id, participant_id, client_id)
            if session is None:
                return None
            now = self._clock()
            session["status"] = "active"
            session["last_seen"] = now
            session["last_seen_at"] = _format_server_time(now)
            self._append_event_locked(session, payload.get("event") or "study_resume_after_reload", payload)
            self._persist()
            return dict(session)

    def record_client_event(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        session_id = str(payload.get("session_id") or "").strip()
        study_id = str(payload.get("study_id") or "").strip()
        participant_id = str(payload.get("participant_id") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        with self._lock:
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                session = self._find_active_locked(study_id, participant_id, client_id)
            if session is None:
                return None
            self._append_event_locked(session, payload.get("event") or "client_event", payload)
            self._persist()
            return dict(session)

    def mark_completed(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(str(session_id or "").strip())
            if session is None:
                return False
            session["status"] = "completed"
            self._persist()
            return True

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(session) for session in self._sessions.values() if session.get("status") == "active"]

    def _find_active_locked(self, study_id: str, participant_id: str, client_id: str) -> dict[str, Any] | None:
        for session in self._sessions.values():
            if session.get("status") != "active":
                continue
            if session.get("study_id") != study_id or session.get("participant_id") != participant_id:
                continue
            if client_id and session.get("client_id") != client_id:
                continue
            return session
        return None

    def _append_event_locked(self, session: dict[str, Any], event: Any, payload: dict[str, Any]) -> None:
        events = session.setdefault("events", [])
        event_name = str(event or "client_event").strip() or "client_event"
        session["current_index"] = payload.get("current_index", session.get("current_index"))
        session["current_type"] = payload.get("current_type", session.get("current_type"))
        item = {
            "event": event_name,
            "received_at": _format_server_time(self._clock()),
            "current_index": payload.get("current_index"),
            "current_type": payload.get("current_type"),
            "is_stimulus_active": bool(payload.get("is_stimulus_active", False)),
        }
        if event_name in INTERRUPTION_EVENTS and item["is_stimulus_active"]:
            item["interrupted_by_reload"] = True
            session["last_interruption"] = item
        events.append(item)
        if len(events) > MAX_EVENTS_PER_SESSION:
            del events[:-MAX_EVENTS_PER_SESSION]


def public_session(session: dict[str, Any] | None) -> dict[str, Any] | None:
    if not session:
        return None
    return {
        "session_id": session.get("session_id"),
        "client_id": session.get("client_id"),
        "study_id": session.get("study_id"),
        "participant_id": session.get("participant_id"),
        "current_index": session.get("current_index"),
        "current_type": session.get("current_type"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "last_seen_at": session.get("last_seen_at"),
        "last_interruption": session.get("last_interruption"),
    }


def _session_matches_payload(
    session: dict[str, Any],
    *,
    study_id: str,
    participant_id: str,
    client_id: str,
) -> bool:
    for key, expected in (
        ("study_id", study_id),
        ("participant_id", participant_id),
        ("client_id", client_id),
    ):
        if expected and str(session.get(key) or "").strip() != expected:
            return False
    return True


def _format_server_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
