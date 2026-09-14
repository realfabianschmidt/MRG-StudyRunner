"""Append-only quality and timing journals, written while recording.

Package 5c (docs/archive/architecture-1.0-umbau.md). One writer per worker process,
shared by every recorder in that session, because the two journals are
session-scoped (target doc §8's layout puts them at the session root) while
recorders are per plugin.

Durability follows the recording's own rhythm rather than inventing a
second one: lines are written as they happen and fsynced on the same
checkpoint tick that already flushes XDF data durably. A crash therefore
loses at most the same window of quality evidence as of recorded data,
which is the honest guarantee -- promising more would mean fsyncing every
line and slowing the ingest loop to protect its own commentary.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from study_runner.contracts.recording_checkpoint import CHECKPOINT_JOURNAL_FILENAME
# Re-exported: the names moved to contracts in 5h so the host can append to
# quality.jsonl during recovery without importing anything worker-side.
from study_runner.contracts.quality_journal import (
    QUALITY_JOURNAL_FILENAME,
    TIMING_JOURNAL_FILENAME,
)

__all__ = [
    "CHECKPOINT_JOURNAL_FILENAME",
    "QUALITY_JOURNAL_FILENAME",
    "TIMING_JOURNAL_FILENAME",
    "SessionJournalWriter",
]


class SessionJournalWriter:
    """Append quality and timing records for one recording session."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir).resolve()
        self._lock = threading.Lock()
        self._handles: dict[str, Any] = {}
        self._closed = False

    def append_quality(self, record: Mapping[str, Any]) -> None:
        self._append(QUALITY_JOURNAL_FILENAME, record)

    def append_timing(self, record: Mapping[str, Any]) -> None:
        self._append(TIMING_JOURNAL_FILENAME, record)

    def append_checkpoint(self, record: Mapping[str, Any]) -> bool:
        """Append a checkpoint and fsync it; ``True`` only if it is on disk.

        Package 5h. Unlike the other two journals this one is durable per
        record and reports whether it succeeded, because a checkpoint is a
        *claim* about durability. Flushing it lazily would let recovery
        confirm a prefix that was never written, which is worse than having
        no checkpoint at all: it would turn an honest "unknown tail" into a
        false "all present".
        """
        self._append(CHECKPOINT_JOURNAL_FILENAME, record)
        with self._lock:
            handle = self._handles.get(CHECKPOINT_JOURNAL_FILENAME)
            if handle is None:
                return False
            try:
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                return False
        return True

    def flush(self, *, durable: bool = False) -> None:
        """Flush both journals; ``durable`` also fsyncs each open file."""
        with self._lock:
            for handle in self._handles.values():
                try:
                    handle.flush()
                    if durable:
                        os.fsync(handle.fileno())
                except OSError:
                    # Journals are evidence about the recording, never a
                    # reason to stop it: a full or read-only disk must not
                    # take down an in-progress session from the side.
                    pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for handle in self._handles.values():
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError:
                    pass
                try:
                    handle.close()
                except OSError:
                    pass
            self._handles.clear()

    def __enter__(self) -> "SessionJournalWriter":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _append(self, filename: str, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            if self._closed:
                return
            handle = self._handles.get(filename)
            if handle is None:
                try:
                    self.session_dir.mkdir(parents=True, exist_ok=True)
                    handle = (self.session_dir / filename).open("a", encoding="utf-8", newline="\n")
                except OSError:
                    return
                self._handles[filename] = handle
            try:
                handle.write(line + "\n")
            except OSError:
                pass
