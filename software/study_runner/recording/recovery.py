"""Persistent 15-minute recording lease used after web-server loss."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping

from study_runner.shared.atomic_io import atomic_write_json

from .errors import WorkerProtocolError


LEASE_SCHEMA = "study-runner/recording-lease/v1"
DEFAULT_RECORDING_LEASE_SECONDS = 15 * 60


@dataclass(frozen=True)
class RecordingLease:
    session_id: str
    worker_generation: int
    lease_seconds: float
    last_refresh_epoch: float
    lease_until_epoch: float
    state: str = "active"
    closed_at_epoch: float | None = None
    close_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": LEASE_SCHEMA,
            "session_id": self.session_id,
            "worker_generation": self.worker_generation,
            "lease_seconds": self.lease_seconds,
            "last_refresh_epoch": self.last_refresh_epoch,
            "lease_until_epoch": self.lease_until_epoch,
            "state": self.state,
            "closed_at_epoch": self.closed_at_epoch,
            "close_reason": self.close_reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecordingLease":
        if payload.get("schema") != LEASE_SCHEMA:
            raise WorkerProtocolError("unsupported recording lease schema")
        return cls(
            session_id=str(payload.get("session_id") or ""),
            worker_generation=int(payload.get("worker_generation") or 0),
            lease_seconds=float(payload.get("lease_seconds") or 0.0),
            last_refresh_epoch=float(payload.get("last_refresh_epoch") or 0.0),
            lease_until_epoch=float(payload.get("lease_until_epoch") or 0.0),
            state=str(payload.get("state") or ""),
            closed_at_epoch=(
                float(payload["closed_at_epoch"]) if payload.get("closed_at_epoch") is not None else None
            ),
            close_reason=str(payload["close_reason"]) if payload.get("close_reason") else None,
        )


class RecordingLeaseStore:
    """Lets the worker close cleanly after Flask has been absent for 15 minutes."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        lease_seconds: float = DEFAULT_RECORDING_LEASE_SECONDS,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.path = Path(path)
        self._clock = clock
        self._lease_seconds = float(lease_seconds)
        self._lock = threading.RLock()

    def start(self, session_id: str, *, worker_generation: int) -> RecordingLease:
        if not session_id.strip() or worker_generation < 1:
            raise ValueError("valid session_id and worker_generation are required")
        with self._lock:
            existing = self.load()
            if existing is not None:
                if existing.session_id != session_id or existing.worker_generation != worker_generation:
                    raise WorkerProtocolError("recording lease belongs to another worker session")
                return existing
            now = self._clock()
            lease = RecordingLease(
                session_id=session_id,
                worker_generation=worker_generation,
                lease_seconds=self._lease_seconds,
                last_refresh_epoch=now,
                lease_until_epoch=now + self._lease_seconds,
            )
            self._save(lease)
            return lease

    def refresh(self, session_id: str, *, worker_generation: int) -> RecordingLease:
        with self._lock:
            lease = self.load()
            if lease is None:
                return self.start(session_id, worker_generation=worker_generation)
            if lease.session_id != session_id or lease.worker_generation != worker_generation:
                raise WorkerProtocolError("recording lease refresh does not match worker")
            if lease.state != "active":
                raise WorkerProtocolError(f"cannot refresh recording lease in state {lease.state}")
            now = self._clock()
            updated = replace(lease, last_refresh_epoch=now, lease_until_epoch=now + lease.lease_seconds)
            self._save(updated)
            return updated

    def restart_generation(self, session_id: str, *, worker_generation: int) -> RecordingLease:
        """Atomically lease a confirmed replacement worker generation."""

        if not session_id.strip() or worker_generation < 1:
            raise ValueError("valid session_id and worker_generation are required")
        with self._lock:
            existing = self.load()
            if existing is not None:
                if existing.session_id != session_id:
                    raise WorkerProtocolError("recording lease belongs to another session")
                if worker_generation <= existing.worker_generation:
                    raise WorkerProtocolError("replacement worker generation must increase")
            now = self._clock()
            lease = RecordingLease(
                session_id=session_id,
                worker_generation=worker_generation,
                lease_seconds=self._lease_seconds,
                last_refresh_epoch=now,
                lease_until_epoch=now + self._lease_seconds,
            )
            self._save(lease)
            return lease

    def expire_if_due(self) -> RecordingLease | None:
        """Transition once to ``expired``; the worker must then close XDFs."""

        with self._lock:
            lease = self.load()
            now = self._clock()
            if lease is None or lease.state != "active" or now < lease.lease_until_epoch:
                return lease
            expired = replace(
                lease,
                state="expired",
                closed_at_epoch=now,
                close_reason="web_server_lease_expired",
            )
            self._save(expired)
            return expired

    def mark_closed(self, *, reason: str) -> RecordingLease:
        with self._lock:
            lease = self.load()
            if lease is None:
                raise WorkerProtocolError("recording lease does not exist")
            closed = replace(
                lease,
                state="closed",
                closed_at_epoch=lease.closed_at_epoch or self._clock(),
                close_reason=reason,
            )
            self._save(closed)
            return closed

    def load(self) -> RecordingLease | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise WorkerProtocolError(f"recording lease is unreadable: {self.path}") from error
        if not isinstance(payload, dict):
            raise WorkerProtocolError("recording lease must be a JSON object")
        return RecordingLease.from_dict(payload)

    def _save(self, lease: RecordingLease) -> None:
        atomic_write_json(self.path, lease.as_dict())
