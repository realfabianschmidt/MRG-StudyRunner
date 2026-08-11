"""Append-only, fsynced audit journals for one study session.

The small JSON files used by :mod:`session_store` and
:mod:`trial_event_service` are convenient projections, but an atomic replace
only preserves their newest state.  This module adds the complementary audit
trail: every acknowledged transition is appended to a session-scoped JSONL
file and flushed to stable storage before the projection is updated.

Each record contains a complete stream snapshot.  That costs a little more
disk space while a study is active, but makes crash recovery deterministic and
keeps schema migration deliberately simple.  Successful finalization writes a
compact, immutable copy into the scientific session folder.  Runtime journals
are retained after archival; they are research/QC evidence, not disposable
recovery scratch files.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable
import uuid

from study_runner.shared.atomic_io import atomic_path_lock, atomic_write_bytes


JOURNAL_SCHEMA = "study-runner/session-journal/v1"
ARCHIVE_SCHEMA = "study-runner/session-journal-archive/v1"
JOURNAL_STREAMS = ("session", "trial")
UNBOUND_SESSION_ID = "__unbound__"
TERMINAL_FINALIZATION_STATUSES = {"completed", "completed_degraded"}


class SessionJournalCorruptionError(RuntimeError):
    """A durable journal contains an invalid non-tail record."""


class SessionJournalArchiveConflictError(RuntimeError):
    """An immutable archive exists for different source journal content."""


class SessionJournalStore:
    """Own session-scoped append, recovery reads, and terminal archival."""

    def __init__(
        self,
        data_dir: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "runtime" / "session-journals"
        self._clock = clock

    def journal_path(self, stream: str, session_id: Any) -> Path:
        normalized_stream = _stream(stream)
        normalized_session_id = _session_id(session_id)
        return self.root / _session_directory_name(normalized_session_id) / f"{normalized_stream}.jsonl"

    def append(
        self,
        stream: str,
        session_id: Any,
        event: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Append and fsync one transition before callers acknowledge it."""

        normalized_stream = _stream(stream)
        normalized_session_id = _session_id(session_id)
        if not isinstance(snapshot, dict):
            raise TypeError("session journal snapshot must be a JSON object")
        record = {
            "schema": JOURNAL_SCHEMA,
            "record_id": str(uuid.uuid4()),
            # ``time_ns`` supplies a process-independent replay order even in
            # tests (and deployments) whose injected scientific clock is
            # fixed. UUID remains the deterministic tie breaker.
            "order_ns": time.time_ns(),
            "recorded_at_epoch": float(self._clock()),
            "stream": normalized_stream,
            "session_id": (
                "" if normalized_session_id == UNBOUND_SESSION_ID else normalized_session_id
            ),
            "event": str(event or "state_updated").strip() or "state_updated",
            "snapshot": deepcopy(snapshot),
        }
        encoded = _canonical_json(record) + b"\n"
        path = self.journal_path(normalized_stream, normalized_session_id)
        _append_fsynced(path, encoded)
        return deepcopy(record)

    def records(
        self,
        stream: str,
        session_id: Any,
    ) -> list[dict[str, Any]]:
        """Read valid records, tolerating only a torn final JSONL line."""

        normalized_stream = _stream(stream)
        normalized_session_id = _session_id(session_id)
        return _read_journal(
            self.journal_path(normalized_stream, normalized_session_id),
            expected_stream=normalized_stream,
            expected_session_id=normalized_session_id,
        )

    def latest_snapshots(self, stream: str) -> dict[str, dict[str, Any]]:
        """Return the newest durable record for every session in ``stream``."""

        normalized_stream = _stream(stream)
        latest: dict[str, dict[str, Any]] = {}
        if not self.root.is_dir():
            return latest
        for path in self.root.glob(f"*/{normalized_stream}.jsonl"):
            records = _read_journal(path, expected_stream=normalized_stream)
            for record in records:
                session_id = _session_id(record.get("session_id"))
                previous = latest.get(session_id)
                if previous is None or _record_order(record) > _record_order(previous):
                    latest[session_id] = record
        return latest

    def archive_session(
        self,
        session_id: Any,
        session_root: Path,
        *,
        finalization_status: str,
    ) -> dict[str, Any]:
        """Publish an immutable compact archive after terminal finalization.

        The caller must supply the terminal state it is about to durably
        commit.  Failed or still-running finalizations cannot archive.  Source
        JSONL files are intentionally retained because they are QC evidence;
        only a verified compact projection is added to the result folder.
        """

        normalized_session_id = _session_id(session_id)
        if normalized_session_id == UNBOUND_SESSION_ID:
            raise ValueError("a concrete session_id is required for archival")
        terminal_status = str(finalization_status or "").strip()
        if terminal_status not in TERMINAL_FINALIZATION_STATUSES:
            raise ValueError("session journals may only be archived for terminal finalization")

        streams: dict[str, Any] = {}
        combined_digest = hashlib.sha256()
        for stream in JOURNAL_STREAMS:
            records = self.records(stream, normalized_session_id)
            canonical_records = _canonical_json(records)
            digest = hashlib.sha256(canonical_records).hexdigest()
            combined_digest.update(stream.encode("ascii"))
            combined_digest.update(b"\0")
            combined_digest.update(canonical_records)
            streams[stream] = {
                "record_count": len(records),
                "sha256": digest,
                "records": records,
            }

        payload = {
            "schema": ARCHIVE_SCHEMA,
            "session_id": normalized_session_id,
            "finalization_status": terminal_status,
            "archived_at_epoch": float(self._clock()),
            "source_sha256": combined_digest.hexdigest(),
            "streams": streams,
        }
        archive_path = Path(session_root) / "logs" / "session-journals.archive.json"
        encoded = _canonical_json(payload) + b"\n"

        with atomic_path_lock(archive_path):
            if archive_path.is_file():
                existing = _read_json_object(archive_path)
                if (
                    existing.get("schema") != ARCHIVE_SCHEMA
                    or existing.get("session_id") != normalized_session_id
                    or existing.get("source_sha256") != payload["source_sha256"]
                ):
                    raise SessionJournalArchiveConflictError(
                        "session journal archive already exists for different source content"
                    )
                return {
                    "path": archive_path.relative_to(Path(session_root)).as_posix(),
                    "source_sha256": existing["source_sha256"],
                    "record_count": sum(
                        int(value.get("record_count") or 0)
                        for value in (existing.get("streams") or {}).values()
                        if isinstance(value, dict)
                    ),
                    "already_archived": True,
                    "runtime_journals_retained": True,
                }

            atomic_write_bytes(archive_path, encoded)
            verified = _read_json_object(archive_path)
            if (
                verified.get("schema") != ARCHIVE_SCHEMA
                or verified.get("session_id") != normalized_session_id
                or verified.get("source_sha256") != payload["source_sha256"]
            ):
                raise RuntimeError("session journal archive verification failed")

        return {
            "path": archive_path.relative_to(Path(session_root)).as_posix(),
            "source_sha256": payload["source_sha256"],
            "record_count": sum(value["record_count"] for value in streams.values()),
            "already_archived": False,
            "runtime_journals_retained": True,
        }


def _append_fsynced(path: Path, encoded: bytes) -> None:
    path = Path(path)
    with atomic_path_lock(path):
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short append while writing session journal")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if not existed:
            _fsync_directory(path.parent)


def _read_journal(
    path: Path,
    *,
    expected_stream: str,
    expected_session_id: str | None = None,
) -> list[dict[str, Any]]:
    if not Path(path).is_file():
        return []
    raw = Path(path).read_bytes()
    lines = raw.splitlines(keepends=True)
    records: list[dict[str, Any]] = []
    for index, raw_line in enumerate(lines):
        content = raw_line.rstrip(b"\r\n")
        if not content:
            continue
        try:
            record = json.loads(content.decode("utf-8"))
            _validate_record(record, expected_stream, expected_session_id)
        except (UnicodeDecodeError, ValueError, TypeError, KeyError) as error:
            is_torn_tail = index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r"))
            if is_torn_tail:
                break
            raise SessionJournalCorruptionError(
                f"invalid durable session journal record {path}:{index + 1}: {error}"
            ) from error
        records.append(record)
    records.sort(key=_record_order)
    return records


def _validate_record(
    record: Any,
    expected_stream: str,
    expected_session_id: str | None,
) -> None:
    if not isinstance(record, dict) or record.get("schema") != JOURNAL_SCHEMA:
        raise ValueError("unsupported session journal schema")
    if record.get("stream") != expected_stream:
        raise ValueError("session journal stream does not match its file")
    if not isinstance(record.get("snapshot"), dict):
        raise ValueError("session journal snapshot is not an object")
    if not str(record.get("record_id") or "").strip():
        raise ValueError("session journal record_id is missing")
    if expected_session_id is not None and _session_id(record.get("session_id")) != expected_session_id:
        raise ValueError("session journal identity does not match its directory")


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _record_order(record: dict[str, Any]) -> tuple[int, str]:
    return int(record.get("order_ns") or 0), str(record.get("record_id") or "")


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _stream(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized not in JOURNAL_STREAMS:
        raise ValueError(f"unsupported session journal stream: {normalized or '<empty>'}")
    return normalized


def _session_id(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized or UNBOUND_SESSION_ID


def _session_directory_name(session_id: str) -> str:
    if session_id == UNBOUND_SESSION_ID:
        return UNBOUND_SESSION_ID
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip(".-_")[:48] or "session"
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return f"{safe}-{digest}"


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
