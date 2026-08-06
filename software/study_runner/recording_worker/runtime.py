"""Command handlers and recovery journal for one detached recording session."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping, Sequence

from study_runner.recording.worker_protocol import WorkerCommand
from study_runner.recording.recovery import RecordingLeaseStore
from study_runner.shared.atomic_io import atomic_write_json

from .core import NativeXdfCore
from .lsl_recording import (
    BackupRecorder,
    LslSourceRecorder,
    ProjectionCache,
    lsl_version_info,
    require_pylsl,
)


MERGE_JOURNAL_SCHEMA = "study-runner/xdf-merge-operations/v1"
WORKER_ATTENTION_SCHEMA = "study-runner/recording-worker-attention/v1"
PLUGIN_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class RecordingWorkerRuntime:
    """Stateful implementation behind the authenticated command router."""

    def __init__(
        self,
        *,
        session_id: str,
        session_dir: Path,
        state_file: Path,
        generation: int,
        token: str,
        core_path: Path,
        lease_seconds: float,
        wall_clock: Any = time.time,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id is required")
        self.session_id = session_id
        self.session_dir = Path(session_dir).resolve()
        self.state_file = Path(state_file).resolve()
        if not self.session_dir.is_dir():
            raise ValueError("session_dir must exist")
        if not self.state_file.is_relative_to(self.session_dir):
            raise ValueError("worker state file must live inside the session")
        self.generation = int(generation)
        self.token = str(token)
        self.core = NativeXdfCore(Path(core_path), require_canonical=True)
        self.pylsl = require_pylsl()
        self.lsl_versions = lsl_version_info(self.pylsl)
        self.cache = ProjectionCache()
        self._wall_clock = wall_clock
        self._lease_lock = threading.RLock()
        self._lease_until_epoch = wall_clock() + float(lease_seconds)
        self._lock = threading.RLock()
        self._sources: dict[str, LslSourceRecorder] = {}
        self._source_configs: dict[str, str] = {}
        self._backup: BackupRecorder | None = None
        self._backup_config: str | None = None
        self._frozen = False
        self._freeze_reason: str | None = None
        self._merged_outputs: list[str] = []
        self.shutdown_event = threading.Event()
        self._monitor_stop = threading.Event()
        self._monitor_thread = threading.Thread(
            target=self._monitor,
            name=f"recording-worker-monitor-g{self.generation}",
            daemon=True,
        )
        self._merge_journal_path = self.session_dir / "logs" / "xdf-merge-operations.json"
        self._log_path = self.session_dir / "logs" / "recording-worker.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._monitor_thread.start()
        self._log(
            "worker_started",
            core=self.core.probe.as_dict(),
            lsl_versions=self.lsl_versions,
        )

    @property
    def handlers(self) -> Mapping[str, Any]:
        return {
            "health": self.health,
            "refresh_lease": self.refresh_lease,
            "start_recording_source": self.start_recording_source,
            "start_backup_projection": self.start_backup_projection,
            "freeze_session": self.freeze_session,
            "merge_xdf": self.merge_xdf,
            "shutdown_session": self.shutdown_session,
        }

    def _require_session(self, command: WorkerCommand) -> None:
        if str(command.payload.get("session_id") or "") != self.session_id:
            raise ValueError("command session_id mismatch")

    def health(self, command: WorkerCommand) -> Mapping[str, Any]:
        self._require_session(command)
        with self._lease_lock:
            lease_until = self._lease_until_epoch
        with self._lock:
            sources = {key: recorder.status() for key, recorder in self._sources.items()}
            backup = self._backup.status() if self._backup is not None else None
            issues: list[dict[str, str]] = []
            for plugin_key, source in sources.items():
                fatal = str(source.get("fatal_error") or "")
                if fatal:
                    issues.append(
                        {"source": plugin_key, "code": "source_fatal_error", "message": fatal}
                    )
                for stream in source.get("streams") or []:
                    stream_key = str(stream.get("key") or "unknown")
                    error = str(stream.get("last_error") or "")
                    if error:
                        issues.append(
                            {
                                "source": f"{plugin_key}.{stream_key}",
                                "code": "lsl_stream_error",
                                "message": error,
                            }
                        )
                    elif not bool(stream.get("header_written")):
                        issues.append(
                            {
                                "source": f"{plugin_key}.{stream_key}",
                                "code": "lsl_stream_unresolved",
                                "message": "declared LSL stream has not opened an XDF header",
                            }
                        )
                    elif bool(stream.get("primary")) and float(
                        stream.get("nominal_rate_hz") or 0.0
                    ) > 0.0:
                        nominal_rate = float(stream["nominal_rate_hz"])
                        maximum_age = max(2.5, 5.0 / nominal_rate)
                        sample_count = int(stream.get("sample_count") or 0)
                        age = stream.get("last_sample_age_seconds")
                        if sample_count < 1 or age is None:
                            issues.append(
                                {
                                    "source": f"{plugin_key}.{stream_key}",
                                    "code": "lsl_primary_no_samples",
                                    "message": "regular primary LSL stream has not delivered a sample",
                                }
                            )
                        elif float(age) > maximum_age:
                            issues.append(
                                {
                                    "source": f"{plugin_key}.{stream_key}",
                                    "code": "lsl_stream_stale",
                                    "message": (
                                        f"primary sample age {float(age):.3f}s exceeds "
                                        f"the {maximum_age:.3f}s health threshold"
                                    ),
                                }
                            )
            if isinstance(backup, Mapping) and backup.get("last_error"):
                issues.append(
                    {
                        "source": "derived_backup",
                        "code": "backup_writer_error",
                        "message": str(backup["last_error"]),
                    }
                )
            return {
                "healthy": not issues,
                "status": "healthy" if not issues else "attention_required",
                "issues": issues,
                "readiness_contract": "fresh-primary/v1",
                "session_id": self.session_id,
                "generation": self.generation,
                "core": self.core.probe.as_dict(),
                "lsl_versions": dict(self.lsl_versions),
                "lease_until_epoch": lease_until,
                "frozen": self._frozen,
                "freeze_reason": self._freeze_reason,
                "sources": sources,
                "backup": backup,
                "merged_outputs": list(self._merged_outputs),
            }

    def refresh_lease(self, command: WorkerCommand) -> Mapping[str, Any]:
        self._require_session(command)
        try:
            deadline = float(command.payload.get("lease_until_epoch"))
        except (TypeError, ValueError) as error:
            raise ValueError("lease_until_epoch must be numeric") from error
        now = self._wall_clock()
        if not math.isfinite(deadline) or deadline <= now or deadline > now + 1800.0:
            raise ValueError("lease_until_epoch must be within the next 30 minutes")
        with self._lease_lock:
            if self._frozen:
                raise RuntimeError("recording session is already frozen")
            self._lease_until_epoch = deadline
        return {"lease_until_epoch": deadline}

    def start_recording_source(self, command: WorkerCommand) -> Mapping[str, Any]:
        self._require_session(command)
        payload = command.payload
        plugin_key = str(payload.get("plugin_key") or "")
        if not PLUGIN_KEY.fullmatch(plugin_key):
            raise ValueError("plugin_key is invalid")
        target = self._session_path(payload.get("target_path"), area="raw/plugins")
        expected_parent = (self.session_dir / "raw" / "plugins" / plugin_key).resolve()
        if target.parent != expected_parent or not re.fullmatch(r"part-\d{4}\.xdf", target.name):
            raise ValueError("recording target does not match the plugin segment contract")
        raw_streams = payload.get("streams")
        if not isinstance(raw_streams, list) or not all(isinstance(item, Mapping) for item in raw_streams):
            raise ValueError("streams must be a non-empty object list")
        config = _canonical_json(
            {
                "target_path": str(target),
                "streams": [dict(item) for item in raw_streams],
                "require_stream_headers": bool(payload.get("require_stream_headers", True)),
                "require_fresh_primary_sample": bool(
                    payload.get("require_fresh_primary_sample", False)
                ),
                "readiness_timeout_seconds": payload.get("readiness_timeout_seconds", 4.0),
                "maximum_primary_sample_age_seconds": payload.get(
                    "maximum_primary_sample_age_seconds", 2.0
                ),
            }
        )
        with self._lock:
            if self._frozen:
                raise RuntimeError("recording session is already frozen")
            existing = self._sources.get(plugin_key)
            if existing is not None:
                if self._source_configs.get(plugin_key) != config:
                    raise RuntimeError("recording source was already started with different settings")
                recorder = existing
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                recorder = LslSourceRecorder(
                    self.core,
                    plugin_key=plugin_key,
                    target_path=target,
                    streams=raw_streams,
                    cache=self.cache,
                    pylsl_module=self.pylsl,
                )
                self._sources[plugin_key] = recorder
                self._source_configs[plugin_key] = config
                recorder.start()
        require_primary = bool(payload.get("require_fresh_primary_sample", False))
        try:
            readiness_timeout = float(payload.get("readiness_timeout_seconds", 4.0))
            maximum_age = float(payload.get("maximum_primary_sample_age_seconds", 2.0))
        except (TypeError, ValueError) as error:
            raise ValueError("recording readiness timings must be numeric") from error
        if not 0.1 <= readiness_timeout <= 8.0 or not 0.1 <= maximum_age <= 30.0:
            raise ValueError("recording readiness timings are outside safe bounds")
        if not recorder.wait_until_ready(
            require_stream_headers=bool(payload.get("require_stream_headers", True)),
            require_fresh_primary_sample=require_primary,
            timeout_seconds=readiness_timeout,
            maximum_sample_age_seconds=maximum_age,
        ):
            status = recorder.status()
            raise RuntimeError(
                "recording source did not become ready before study onset: "
                + json.dumps(status, ensure_ascii=False, sort_keys=True)
            )
        self._log("recording_source_started", plugin_key=plugin_key, target_path=str(target))
        return recorder.status()

    def start_backup_projection(self, command: WorkerCommand) -> Mapping[str, Any]:
        self._require_session(command)
        target = self._session_path(command.payload.get("target_path"), area="raw/backup")
        if target.suffix.lower() != ".xdf":
            raise ValueError("backup target must be an XDF")
        config = _canonical_json(dict(command.payload))
        with self._lock:
            if self._frozen:
                raise RuntimeError("recording session is already frozen")
            if self._backup is not None:
                if self._backup_config != config:
                    raise RuntimeError("backup projection was already started with different settings")
                return self._backup.status()
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = BackupRecorder(
                self.core,
                target_path=target,
                payload=command.payload,
                cache=self.cache,
                pylsl_module=self.pylsl,
            )
            self._backup = backup
            self._backup_config = config
            backup.start()
        self._log("backup_started", target_path=str(target), rate_hz=command.payload.get("rate_hz"))
        return backup.status()

    def freeze_session(self, command: WorkerCommand) -> Mapping[str, Any]:
        self._require_session(command)
        return self.freeze(reason="submission_finalization")

    def freeze(self, *, reason: str) -> Mapping[str, Any]:
        with self._lock:
            if self._frozen:
                return {
                    "already_frozen": True,
                    "reason": self._freeze_reason,
                    "sources": {key: value.status() for key, value in self._sources.items()},
                    "backup": self._backup.status() if self._backup is not None else None,
                }
            source_results: dict[str, Any] = {}
            failures: list[str] = []
            for plugin_key, recorder in self._sources.items():
                try:
                    source_results[plugin_key] = recorder.freeze(reason=reason)
                except Exception as error:
                    failures.append(f"{plugin_key}: {type(error).__name__}: {error}")
                    try:
                        recorder.abort()
                    except Exception:
                        pass
            backup_result: Mapping[str, Any] | None = None
            if self._backup is not None:
                try:
                    backup_result = self._backup.freeze(reason=reason)
                except Exception as error:
                    failures.append(f"derived_backup: {type(error).__name__}: {error}")
                    try:
                        self._backup.abort()
                    except Exception:
                        pass
            self._frozen = True
            self._freeze_reason = reason
            for plugin_key, result in source_results.items():
                fatal = result.get("fatal_error") if isinstance(result, Mapping) else None
                if fatal:
                    failures.append(f"{plugin_key}: {fatal}")
                for stream in result.get("streams", []) if isinstance(result, Mapping) else []:
                    if not stream.get("header_written") or stream.get("last_error"):
                        failures.append(
                            f"{plugin_key}.{stream.get('key')}: "
                            f"{stream.get('last_error') or 'declared stream was never resolved'}"
                        )
            if isinstance(backup_result, Mapping) and backup_result.get("last_error"):
                failures.append(f"derived_backup: {backup_result['last_error']}")
            self._log("recording_frozen", reason=reason, failures=failures)
            result = {
                "reason": reason,
                "sources": source_results,
                "backup": backup_result,
                "quality_failures": failures,
            }
            if failures:
                self._write_attention("recording_freeze_quality_failure", failures=failures)
            return result

    def merge_xdf(self, command: WorkerCommand) -> Mapping[str, Any]:
        self._require_session(command)
        payload = command.payload
        with self._lock:
            if not self._frozen:
                raise RuntimeError("recording must be frozen before merge")
        if payload.get("preserve_native_timestamps") is not True:
            raise ValueError("merge must preserve native timestamps")
        if payload.get("preserve_clock_offsets") is not True:
            raise ValueError("merge must preserve clock offsets")
        if payload.get("resample") is not False or payload.get("atomic_publish") is not True:
            raise ValueError("merge resampling/publication contract mismatch")
        operation_id = str(payload.get("operation_id") or "").strip()
        if not re.fullmatch(r"xdf-merge-[0-9a-f]{32}", operation_id):
            raise ValueError("merge operation_id is invalid")
        raw_sources = payload.get("source_artifacts")
        source_paths = payload.get("source_paths")
        if not isinstance(raw_sources, list) or not isinstance(source_paths, list):
            raise ValueError("merge source_artifacts/source_paths are required")
        if len(raw_sources) != len(source_paths) or not raw_sources:
            raise ValueError("merge source lists do not match")
        sources: list[tuple[str, Path]] = []
        source_manifest: list[dict[str, str]] = []
        for index, raw in enumerate(raw_sources):
            if not isinstance(raw, Mapping):
                raise ValueError("merge source artifact must be an object")
            path = self._session_path(raw.get("path"), area="raw")
            if str(Path(str(source_paths[index])).resolve()) != str(path):
                raise ValueError("merge source path order mismatch")
            source_key = str(raw.get("source_key") or "")
            if not PLUGIN_KEY.fullmatch(source_key):
                raise ValueError("merge source_key is invalid")
            expected_hash = str(raw.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                raise ValueError("merge source sha256 is invalid")
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                raise RuntimeError(f"merge source changed before merge: {path.name}")
            sources.append((source_key, path))
            source_manifest.append(
                {"path": str(path), "source_key": source_key, "sha256": actual_hash}
            )
        output = self._session_path(payload.get("output_path"), area="derived")
        temporary = self._session_path(payload.get("temporary_output_path"), area="derived")
        if output.suffix.lower() != ".xdf" or temporary.suffix.lower() != ".tmp":
            raise ValueError("merge output/temporary suffix contract mismatch")
        if output == temporary:
            raise ValueError("merge output and temporary path must differ")
        output.parent.mkdir(parents=True, exist_ok=True)
        request_fingerprint = hashlib.sha256(
            _canonical_json(
                {
                    "operation_id": operation_id,
                    "sources": source_manifest,
                    "output": str(output),
                    "temporary": str(temporary),
                }
            ).encode("utf-8")
        ).hexdigest()
        journal = self._load_merge_journal()
        existing = journal["operations"].get(operation_id)
        if existing is not None and existing.get("fingerprint") != request_fingerprint:
            raise RuntimeError("merge operation_id was reused with different inputs")
        if existing is not None and existing.get("state") == "completed":
            if not output.is_file() or sha256_file(output) != existing.get("output_sha256"):
                raise RuntimeError("completed merge journal does not match its output")
            return dict(existing.get("result") or {})
        if output.exists():
            output_hash = sha256_file(output) if output.is_file() else ""
            expected_published_hash = str((existing or {}).get("temporary_sha256") or "")
            if (
                expected_published_hash
                and output_hash == expected_published_hash
                and (not temporary.exists() or sha256_file(temporary) == output_hash)
            ):
                if temporary.is_file():
                    temporary.unlink()
                result = self._finish_merge_journal(
                    journal,
                    operation_id,
                    request_fingerprint,
                    output,
                    existing.get("native_report") if isinstance(existing, Mapping) else {},
                )
                return result
            raise RuntimeError("merge output already exists without a completed matching journal")
        if temporary.exists():
            interrupted = _next_interrupted_path(temporary, operation_id)
            temporary.replace(interrupted)
            self._log("merge_partial_preserved", operation_id=operation_id, path=str(interrupted))
        journal["operations"][operation_id] = {
            "fingerprint": request_fingerprint,
            "state": "running",
            "started_at_epoch": self._wall_clock(),
            "sources": source_manifest,
            "output": str(output),
            "temporary": str(temporary),
        }
        atomic_write_json(self._merge_journal_path, journal)
        try:
            native_report = self.core.merge(sources, temporary, durable=True)
            temporary_hash = sha256_file(temporary)
            journal["operations"][operation_id].update(
                state="publishing",
                temporary_sha256=temporary_hash,
                native_report=native_report,
            )
            atomic_write_json(self._merge_journal_path, journal)
            try:
                os.link(temporary, output)
            except FileExistsError as error:
                raise RuntimeError("merge output appeared during atomic publication") from error
            _fsync_directory(output.parent)
            if sha256_file(output) != temporary_hash:
                raise RuntimeError("published merge checksum mismatch")
            temporary.unlink()
            result = self._finish_merge_journal(
                journal,
                operation_id,
                request_fingerprint,
                output,
                native_report,
            )
            with self._lock:
                self._merged_outputs.append(str(output))
            self._log("merge_completed", **result)
            return result
        except Exception as error:
            entry = journal["operations"][operation_id]
            entry.update(
                state="failed",
                failed_at_epoch=self._wall_clock(),
                error=f"{type(error).__name__}: {error}",
            )
            atomic_write_json(self._merge_journal_path, journal)
            raise

    def _finish_merge_journal(
        self,
        journal: dict[str, Any],
        operation_id: str,
        fingerprint: str,
        output: Path,
        native_report: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        output_hash = sha256_file(output)
        result = {
            "operation_id": operation_id,
            "output_path": str(output),
            "output_sha256": output_hash,
            "native_report": dict(native_report or {}),
        }
        journal["operations"][operation_id] = {
            **dict(journal["operations"].get(operation_id) or {}),
            "fingerprint": fingerprint,
            "state": "completed",
            "finished_at_epoch": self._wall_clock(),
            "output_sha256": output_hash,
            "result": result,
        }
        atomic_write_json(self._merge_journal_path, journal)
        return result

    def shutdown_session(self, command: WorkerCommand) -> Mapping[str, Any]:
        self._require_session(command)
        with self._lock:
            if not self._frozen:
                raise RuntimeError("recording worker cannot shut down before freeze")
        return {"shutdown_requested": True}

    def abort_on_worker_failure(self) -> None:
        """Retain partial fragments if the Python worker itself exits unexpectedly."""

        self._monitor_stop.set()
        with self._lock:
            if self._frozen:
                return
            for recorder in self._sources.values():
                try:
                    recorder.abort()
                except Exception:
                    pass
            if self._backup is not None:
                try:
                    self._backup.abort()
                except Exception:
                    pass
            self._write_attention("python_worker_exit_before_freeze")

    def close_monitor(self) -> None:
        self._monitor_stop.set()
        if threading.current_thread() is not self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)

    def _monitor(self) -> None:
        while not self._monitor_stop.wait(1.0):
            try:
                if self._generation_was_replaced():
                    self.freeze(reason="superseded_worker_generation")
                    self.shutdown_event.set()
                    return
                with self._lease_lock:
                    expired = not self._frozen and self._wall_clock() >= self._lease_until_epoch
                if expired:
                    lease_path = self.session_dir / "recording-lease.json"
                    if lease_path.is_file():
                        RecordingLeaseStore(lease_path, clock=self._wall_clock).expire_if_due()
                    self.freeze(reason="web_server_lease_expired")
                    self._write_attention("web_server_lease_expired")
            except Exception as error:
                self._write_attention(
                    "recording_worker_monitor_failed",
                    error=f"{type(error).__name__}: {error}",
                )

    def _generation_was_replaced(self) -> bool:
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            int(payload.get("generation") or 0) != self.generation
            or str(payload.get("token") or "") != self.token
        )

    def _session_path(self, raw: Any, *, area: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("artifact path is required")
        path = Path(raw).resolve()
        area_root = (self.session_dir / Path(area)).resolve()
        if not path.is_relative_to(area_root):
            raise ValueError(f"artifact path must live below session {area}")
        return path

    def _load_merge_journal(self) -> dict[str, Any]:
        if not self._merge_journal_path.is_file():
            return {"schema": MERGE_JOURNAL_SCHEMA, "operations": {}}
        payload = json.loads(self._merge_journal_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != MERGE_JOURNAL_SCHEMA:
            raise RuntimeError("merge operation journal has an unsupported schema")
        if not isinstance(payload.get("operations"), dict):
            raise RuntimeError("merge operation journal is invalid")
        return payload

    def _write_attention(self, reason: str, **details: Any) -> None:
        path = self.session_dir / "recording-worker-attention.json"
        atomic_write_json(
            path,
            {
                "schema": WORKER_ATTENTION_SCHEMA,
                "session_id": self.session_id,
                "generation": self.generation,
                "reason": reason,
                "at_epoch": self._wall_clock(),
                **details,
            },
        )
        self._log("attention_required", reason=reason, **details)

    def _log(self, event: str, **details: Any) -> None:
        entry = {
            "at_epoch": self._wall_clock(),
            "event": event,
            "session_id": self.session_id,
            "generation": self.generation,
            **details,
        }
        encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with self._log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded + "\n")
                handle.flush()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _next_interrupted_path(temporary: Path, operation_id: str) -> Path:
    for index in range(1, 10000):
        candidate = temporary.with_name(
            f"{temporary.name}.interrupted-{operation_id}-{index:04d}.xdf"
        )
        if not candidate.exists():
            return candidate
    raise RuntimeError("too many interrupted merge artifacts")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
