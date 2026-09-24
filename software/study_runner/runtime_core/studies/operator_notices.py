"""Messages the admin must see: every error the tablet shows, and every server
refusal of a participant action.

A participant-facing error is never the only trace of a problem. The server
records its own refusals here (so a notice exists even when the tablet cannot
report anything), and the tablet reports each error it shows through its
client-event channel. The admin dashboard polls ``unacknowledged()`` and shows
each notice as a toast plus an entry that stays until it is clicked.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from study_runner.shared.atomic_io import atomic_write_json


SEVERITIES = ("error", "warning", "info")
MAX_NOTICES = 200
MAX_MESSAGE_LENGTH = 500


class OperatorNoticeStore:
    def __init__(self, data_dir: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(data_dir) / "runtime" / "operator_notices.json"
        self._clock = clock
        self._lock = threading.RLock()
        self._notices: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"[NOTICES] Could not read {self.path.name}: {error}")
            return
        if isinstance(loaded, list):
            self._notices = [notice for notice in loaded if isinstance(notice, dict)]

    def add(
        self,
        message: str,
        *,
        severity: str = "error",
        source: str = "server",
        code: str = "",
        study_id: str = "",
        session_id: str = "",
        participant_id: str = "",
    ) -> dict[str, Any]:
        text = str(message or "").strip()[:MAX_MESSAGE_LENGTH] or "Unknown problem"
        now = float(self._clock())
        notice = {
            "id": f"notice-{uuid.uuid4().hex[:12]}",
            "severity": severity if severity in SEVERITIES else "error",
            "source": str(source or "server"),
            "code": str(code or ""),
            "message": text,
            "study_id": str(study_id or ""),
            "session_id": str(session_id or ""),
            "participant_id": str(participant_id or ""),
            "created_at_epoch": now,
            "acknowledged_at_epoch": None,
        }
        with self._lock:
            self._notices.append(notice)
            self._notices = self._notices[-MAX_NOTICES:]
            atomic_write_json(self.path, self._notices)
        return copy.deepcopy(notice)

    def unacknowledged(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(n) for n in self._notices if n.get("acknowledged_at_epoch") is None]

    def acknowledge(self, notice_id: str = "") -> int:
        """Acknowledge one notice, or all open ones when no id is given."""
        now = float(self._clock())
        count = 0
        with self._lock:
            for notice in self._notices:
                if notice.get("acknowledged_at_epoch") is not None:
                    continue
                if notice_id and notice.get("id") != notice_id:
                    continue
                notice["acknowledged_at_epoch"] = now
                count += 1
            if count:
                atomic_write_json(self.path, self._notices)
        return count
