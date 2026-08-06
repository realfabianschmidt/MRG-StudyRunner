"""Persisted operator-controlled run state for the single-tablet study flow."""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from study_runner.shared.atomic_io import atomic_write_json


INITIAL_STATUS = "loaded"
RUNNING_STATUS = "running"
COMPLETED_STATUS = "completed"
STOPPED_STATUS = "stopped"


class StudyRunStateStore:
    """Track the loaded study and whether the admin has pressed Play.

    The active study config still remains the source of truth for cards and
    settings. This tiny state file only gates the participant page so a tablet
    can be parked in the waiting room until the operator starts the run.
    """

    def __init__(self, data_dir: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "runtime" / "study_run_state.json"
        self._clock = clock
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"[STUDY-RUN] Could not read {self.path.name}: {error}")
            return
        if isinstance(loaded, dict):
            self._state = loaded

    def _persist_locked(self) -> None:
        atomic_write_json(self.path, self._state)

    def ensure_loaded(self, study_id: str) -> dict[str, Any]:
        """Initialize state for the current config without disturbing a run."""
        normalized = _clean_study_id(study_id)
        with self._lock:
            if self._state.get("status") == RUNNING_STATUS and self._state.get("study_id") == normalized:
                return self.public()
            if self._state.get("study_id") == normalized and self._state.get("status") in {
                INITIAL_STATUS,
                COMPLETED_STATUS,
                STOPPED_STATUS,
            }:
                return self.public()
            return self.set_loaded(normalized)

    def set_loaded(self, study_id: str) -> dict[str, Any]:
        normalized = _clean_study_id(study_id)
        now = self._now()
        with self._lock:
            previous_sequence = int(self._state.get("sequence") or 0)
            self._state = {
                "status": INITIAL_STATUS,
                "study_id": normalized,
                "run_id": "",
                "sequence": previous_sequence + 1,
                "loaded_at": _format_time(now),
                "loaded_at_epoch": now,
                "started_at": None,
                "started_at_epoch": None,
                "completed_at": None,
                "completed_at_epoch": None,
                "completed_session_id": "",
                "active_client_id": "",
                "updated_at": _format_time(now),
                "updated_at_epoch": now,
            }
            self._persist_locked()
            return self.public()

    def start(self, study_id: str, active_client_id: str = "") -> dict[str, Any]:
        normalized = _clean_study_id(study_id)
        client_id = str(active_client_id or "").strip()
        now = self._now()
        with self._lock:
            if self._state.get("status") == RUNNING_STATUS and self._state.get("study_id") == normalized:
                return self.public()

            previous_sequence = int(self._state.get("sequence") or 0)
            loaded_at = self._state.get("loaded_at") if self._state.get("study_id") == normalized else None
            loaded_at_epoch = self._state.get("loaded_at_epoch") if self._state.get("study_id") == normalized else None
            self._state = {
                "status": RUNNING_STATUS,
                "study_id": normalized,
                "run_id": f"study-run-{uuid.uuid4()}",
                "sequence": previous_sequence + 1,
                "loaded_at": loaded_at or _format_time(now),
                "loaded_at_epoch": loaded_at_epoch or now,
                "started_at": _format_time(now),
                "started_at_epoch": now,
                "completed_at": None,
                "completed_at_epoch": None,
                "completed_session_id": "",
                "active_client_id": client_id,
                "updated_at": _format_time(now),
                "updated_at_epoch": now,
            }
            self._persist_locked()
            return self.public()

    def complete(self, study_id: str, session_id: str) -> dict[str, Any]:
        normalized = _clean_study_id(study_id)
        now = self._now()
        with self._lock:
            previous_sequence = int(self._state.get("sequence") or 0)
            self._state = {
                **self._state,
                "status": COMPLETED_STATUS,
                "study_id": normalized or str(self._state.get("study_id") or ""),
                "sequence": previous_sequence + 1,
                "completed_at": _format_time(now),
                "completed_at_epoch": now,
                "completed_session_id": str(session_id or "").strip(),
                "active_client_id": str(self._state.get("active_client_id") or "").strip(),
                "updated_at": _format_time(now),
                "updated_at_epoch": now,
            }
            self._persist_locked()
            return self.public()

    def stop(self, study_id: str = "") -> dict[str, Any]:
        normalized = _clean_study_id(study_id or self._state.get("study_id") or "")
        now = self._now()
        with self._lock:
            previous_sequence = int(self._state.get("sequence") or 0)
            self._state = {
                **self._state,
                "status": STOPPED_STATUS,
                "study_id": normalized,
                "sequence": previous_sequence + 1,
                "active_client_id": str(self._state.get("active_client_id") or "").strip(),
                "updated_at": _format_time(now),
                "updated_at_epoch": now,
            }
            self._persist_locked()
            return self.public()

    def public(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state or {"status": INITIAL_STATUS, "study_id": ""})

    def _now(self) -> float:
        return float(self._clock())


def _clean_study_id(value: Any) -> str:
    return str(value or "").strip()


def _format_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
