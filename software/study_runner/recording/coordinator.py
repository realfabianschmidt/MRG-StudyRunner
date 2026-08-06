"""Session-scoped orchestration around the external recording worker.

No XDF bytes are created here.  The coordinator allocates append-never segment
paths and sends idempotent commands to the bundled native worker.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from study_runner.backend.services.shared.atomic_io import atomic_write_json

from .artifacts import ArtifactPaths, sha256_file
from .errors import WorkerProtocolError
from .worker_protocol import LoopbackWorkerClient, WorkerResponse


SEGMENT_LEDGER_SCHEMA = "study-runner/xdf-segment-ledger/v1"
_SEGMENT_FILE = re.compile(r"^part-(\d{4})\.xdf$")
_OPEN_STATES = {"allocated", "recording", "closing"}


@dataclass(frozen=True)
class SegmentRecord:
    plugin_key: str
    number: int
    relative_path: str
    allocation_id: str
    state: str
    created_at_epoch: float
    started_at_epoch: float | None = None
    closed_at_epoch: float | None = None
    close_reason: str | None = None
    worker_generation: int | None = None

    @property
    def filename(self) -> str:
        return f"part-{self.number:04d}.xdf"

    def as_dict(self) -> dict[str, Any]:
        return {
            "plugin_key": self.plugin_key,
            "number": self.number,
            "relative_path": self.relative_path,
            "allocation_id": self.allocation_id,
            "state": self.state,
            "created_at_epoch": self.created_at_epoch,
            "started_at_epoch": self.started_at_epoch,
            "closed_at_epoch": self.closed_at_epoch,
            "close_reason": self.close_reason,
            "worker_generation": self.worker_generation,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SegmentRecord":
        return cls(
            plugin_key=str(payload.get("plugin_key") or ""),
            number=int(payload.get("number") or 0),
            relative_path=str(payload.get("relative_path") or ""),
            allocation_id=str(payload.get("allocation_id") or ""),
            state=str(payload.get("state") or ""),
            created_at_epoch=float(payload.get("created_at_epoch") or 0.0),
            started_at_epoch=(
                float(payload["started_at_epoch"]) if payload.get("started_at_epoch") is not None else None
            ),
            closed_at_epoch=(
                float(payload["closed_at_epoch"]) if payload.get("closed_at_epoch") is not None else None
            ),
            close_reason=str(payload["close_reason"]) if payload.get("close_reason") else None,
            worker_generation=(
                int(payload["worker_generation"]) if payload.get("worker_generation") is not None else None
            ),
        )


class SegmentLedger:
    """Allocates immutable ``part-NNNN.xdf`` paths for one recording plugin."""

    def __init__(
        self,
        paths: ArtifactPaths,
        plugin_key: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.paths = paths
        self.plugin_key = plugin_key
        self.directory = paths.plugin_dir(plugin_key)
        self.path = self.directory / "segments.json"
        self._clock = clock
        self._lock = threading.RLock()

    def allocate(self, allocation_id: str, *, worker_generation: int | None = None) -> SegmentRecord:
        """Allocate once for ``allocation_id`` and never append an old segment.

        A different allocation id means a new worker start/recovery attempt. Any
        prior open segment is marked interrupted and the next number is used.
        Existing XDF files are scanned as a second guard if the ledger was not
        yet present when a machine crashed.
        """

        if not allocation_id.strip():
            raise ValueError("allocation_id is required")
        with self._lock:
            document = self._load()
            records = [SegmentRecord.from_dict(item) for item in document["segments"]]
            for record in records:
                if record.allocation_id == allocation_id:
                    return record

            now = self._clock()
            records = [
                replace(
                    record,
                    state="interrupted",
                    closed_at_epoch=now,
                    close_reason="superseded_by_new_segment",
                )
                if record.state in _OPEN_STATES
                else record
                for record in records
            ]
            next_number = max(
                [record.number for record in records] + self._existing_segment_numbers() + [0]
            ) + 1
            relative_path = (self.directory.relative_to(self.paths.root) / f"part-{next_number:04d}.xdf").as_posix()
            record = SegmentRecord(
                plugin_key=self.plugin_key,
                number=next_number,
                relative_path=relative_path,
                allocation_id=allocation_id,
                state="allocated",
                created_at_epoch=now,
                worker_generation=worker_generation,
            )
            records.append(record)
            self._save(records)
            return record

    def mark_recording(self, allocation_id: str) -> SegmentRecord:
        return self._transition(allocation_id, allowed={"allocated", "recording"}, state="recording")

    def mark_closed(self, allocation_id: str, *, reason: str = "worker_freeze") -> SegmentRecord:
        return self._transition(
            allocation_id,
            allowed=_OPEN_STATES | {"closed"},
            state="closed",
            close_reason=reason,
        )

    def mark_invalid(self, allocation_id: str, *, reason: str) -> SegmentRecord:
        return self._transition(
            allocation_id,
            allowed=_OPEN_STATES | {"closed", "invalid"},
            state="invalid",
            close_reason=reason,
        )

    def records(self) -> tuple[SegmentRecord, ...]:
        with self._lock:
            return tuple(SegmentRecord.from_dict(item) for item in self._load()["segments"])

    def absolute_path(self, record: SegmentRecord) -> Path:
        path = (self.paths.root / record.relative_path).resolve()
        if not path.is_relative_to(self.paths.root.resolve()):
            raise WorkerProtocolError("segment ledger contains a path outside its session")
        return path

    def _transition(
        self,
        allocation_id: str,
        *,
        allowed: set[str],
        state: str,
        close_reason: str | None = None,
    ) -> SegmentRecord:
        with self._lock:
            records = [SegmentRecord.from_dict(item) for item in self._load()["segments"]]
            for index, record in enumerate(records):
                if record.allocation_id != allocation_id:
                    continue
                if record.state == state:
                    return record
                if record.state not in allowed:
                    raise WorkerProtocolError(
                        f"segment {record.filename} cannot transition from {record.state} to {state}"
                    )
                now = self._clock()
                updated = replace(
                    record,
                    state=state,
                    started_at_epoch=(record.started_at_epoch or now) if state == "recording" else record.started_at_epoch,
                    closed_at_epoch=now if state in {"closed", "invalid"} else record.closed_at_epoch,
                    close_reason=close_reason or record.close_reason,
                )
                records[index] = updated
                self._save(records)
                return updated
        raise KeyError(f"unknown segment allocation_id: {allocation_id}")

    def _existing_segment_numbers(self) -> list[int]:
        if not self.directory.is_dir():
            return []
        numbers: list[int] = []
        for child in self.directory.iterdir():
            match = _SEGMENT_FILE.fullmatch(child.name)
            if match:
                numbers.append(int(match.group(1)))
        return numbers

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": SEGMENT_LEDGER_SCHEMA, "plugin_key": self.plugin_key, "segments": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise WorkerProtocolError(f"segment ledger is unreadable: {self.path}") from error
        if not isinstance(payload, dict) or payload.get("schema") != SEGMENT_LEDGER_SCHEMA:
            raise WorkerProtocolError("unsupported segment ledger schema")
        if payload.get("plugin_key") != self.plugin_key or not isinstance(payload.get("segments"), list):
            raise WorkerProtocolError("segment ledger does not match its plugin")
        return payload

    def _save(self, records: Sequence[SegmentRecord]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self.path,
            {
                "schema": SEGMENT_LEDGER_SCHEMA,
                "plugin_key": self.plugin_key,
                "segments": [record.as_dict() for record in records],
            },
        )


class RecordingCoordinator:
    """Thin orchestration facade consumed by the finalization state machine."""

    def __init__(self, paths: ArtifactPaths, worker: LoopbackWorkerClient) -> None:
        self.paths = paths
        self.worker = worker

    def start_plugin(
        self,
        plugin_key: str,
        streams: Sequence[Mapping[str, Any]],
        *,
        command_id: str,
        require_stream_headers: bool = True,
        require_fresh_primary_sample: bool = False,
        readiness_timeout_seconds: float = 4.0,
        maximum_primary_sample_age_seconds: float = 2.0,
    ) -> WorkerResponse:
        ledger = SegmentLedger(self.paths, plugin_key)
        record = ledger.allocate(command_id, worker_generation=self.worker.endpoint.generation)
        if record.state not in {"allocated", "recording"}:
            raise WorkerProtocolError(
                f"recording allocation {command_id!r} is already in terminal state {record.state}"
            )
        target = ledger.absolute_path(record)
        target.parent.mkdir(parents=True, exist_ok=True)
        response = self.worker.send(
            "start_recording_source",
            {
                "session_id": self.paths.identity.session_id,
                "plugin_key": plugin_key,
                "segment_number": record.number,
                "target_path": str(target),
                "streams": [dict(stream) for stream in streams],
                "require_stream_headers": bool(require_stream_headers),
                "require_fresh_primary_sample": bool(require_fresh_primary_sample),
                "readiness_timeout_seconds": float(readiness_timeout_seconds),
                "maximum_primary_sample_age_seconds": float(
                    maximum_primary_sample_age_seconds
                ),
            },
            command_id=command_id,
        )
        if response.ok:
            ledger.mark_recording(command_id)
        return response

    def freeze(self, *, command_id: str) -> WorkerResponse:
        response = self.worker.send(
            "freeze_session",
            {"session_id": self.paths.identity.session_id},
            command_id=command_id,
        )
        if response.ok:
            for ledger in self._all_ledgers():
                for record in ledger.records():
                    if record.state in _OPEN_STATES:
                        ledger.mark_closed(record.allocation_id, reason="worker_freeze")
        return response

    def merge(
        self,
        source_paths: Iterable[Path],
        output_path: Path,
        *,
        command_id: str,
    ) -> WorkerResponse:
        sources = [self._require_session_path(path) for path in source_paths]
        output = self._require_session_path(output_path)
        if not output.is_relative_to(self.paths.derived_dir.resolve()):
            raise ValueError("merged XDF output must live below the session derived directory")
        output.parent.mkdir(parents=True, exist_ok=True)
        source_artifacts = [
            {
                "path": str(path),
                "source_key": self._source_key(path),
                "sha256": sha256_file(path),
            }
            for path in sources
        ]
        operation_payload = {
            "session_id": self.paths.identity.session_id,
            "sources": [
                {
                    "relative_path": Path(artifact["path"])
                    .relative_to(self.paths.root.resolve())
                    .as_posix(),
                    "source_key": artifact["source_key"],
                    "sha256": artifact["sha256"],
                }
                for artifact in source_artifacts
            ],
            "output": output.relative_to(self.paths.root.resolve()).as_posix(),
        }
        operation_id = "xdf-merge-" + hashlib.sha256(
            json.dumps(
                operation_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:32]
        return self.worker.send(
            "merge_xdf",
            {
                "session_id": self.paths.identity.session_id,
                # Stable across command-level retries. The native worker must
                # reconcile this persistent operation before starting another
                # chunk merger after an ambiguous transport failure.
                "operation_id": operation_id,
                "source_paths": [str(path) for path in sources],
                # The native merger must persist
                # ``study_runner_origin_id=<source_key>:<filename>:<index>``
                # and ``study_runner_plugin_key=<source_key>`` on every output
                # stream. Keeping source_key explicit makes
                # parity deterministic even for the derived backup artifact.
                "source_artifacts": source_artifacts,
                "output_path": str(output),
                "preserve_native_timestamps": True,
                "preserve_clock_offsets": True,
                "resample": False,
                "atomic_publish": True,
                "temporary_output_path": str(output.with_name(f".{output.name}.{operation_id}.tmp")),
            },
            command_id=command_id,
        )

    def shutdown(self, *, command_id: str) -> WorkerResponse:
        """Ask a frozen/merged worker to exit after acknowledging the command."""

        return self.worker.send(
            "shutdown_session",
            {"session_id": self.paths.identity.session_id},
            command_id=command_id,
        )

    def _source_key(self, path: Path) -> str:
        resolved = path.resolve()
        plugins_root = self.paths.raw_plugins_dir.resolve()
        backup_root = self.paths.raw_backup_dir.resolve()
        if resolved.is_relative_to(plugins_root):
            relative = resolved.relative_to(plugins_root)
            if len(relative.parts) >= 2:
                return relative.parts[0]
        if resolved.is_relative_to(backup_root):
            return "derived_backup"
        raise ValueError(f"XDF source is not a declared plugin or backup artifact: {path}")

    def _all_ledgers(self) -> list[SegmentLedger]:
        if not self.paths.raw_plugins_dir.is_dir():
            return []
        ledgers: list[SegmentLedger] = []
        for directory in self.paths.raw_plugins_dir.iterdir():
            ledger_path = directory / "segments.json"
            if not directory.is_dir() or not ledger_path.is_file():
                continue
            try:
                payload = json.loads(ledger_path.read_text(encoding="utf-8"))
                plugin_key = str(payload.get("plugin_key") or "")
            except (OSError, ValueError) as error:
                raise WorkerProtocolError(f"segment ledger is unreadable: {ledger_path}") from error
            if not plugin_key:
                raise WorkerProtocolError(f"segment ledger has no plugin key: {ledger_path}")
            ledgers.append(SegmentLedger(self.paths, plugin_key))
        return ledgers

    def _require_session_path(self, path: Path) -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.paths.root.resolve()):
            raise ValueError(f"artifact path is outside the session: {path}")
        return resolved
