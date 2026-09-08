"""Crash-safe background upload jobs for completed study sessions.

The local result save is the commit point. Network destinations are represented
by small journaled jobs afterwards, so a slow or unavailable service never
keeps the participant waiting and a server restart never loses pending work.
"""
from __future__ import annotations

import datetime as dt
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any, Callable

from study_runner.shared.atomic_io import atomic_write_json


BACKOFF_SECONDS = (30, 120, 600, 1800)
MAX_RETRY_AGE_SECONDS = 48 * 60 * 60
DEFAULT_STATUS_DAYS = 7
MAX_STATUS_DAYS = 90

Executor = Callable[[dict[str, Any]], dict[str, Any] | None]
SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class UploadJobError(RuntimeError):
    """Raised for invalid job requests or unavailable payloads."""


def retry_delay_seconds(attempts: int) -> int:
    """Return the roadmap backoff: 30 s, 2 m, 10 m, 30 m, then hourly."""
    normalized = max(1, int(attempts))
    if normalized <= len(BACKOFF_SECONDS):
        return BACKOFF_SECONDS[normalized - 1]
    return 60 * 60


class UploadJobService:
    def __init__(
        self,
        data_dir: Path,
        *,
        executors: dict[str, Executor] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.payload_dir = self.data_dir / "upload_jobs"
        self.journal_path = self.data_dir / "upload_jobs.jsonl"
        self.legacy_notion_queue_path = self.data_dir / "notion_upload_queue.jsonl"
        self._executors = dict(executors or {})
        self._clock = clock
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        self._replay_journal()
        self._recover_interrupted_attempts()

    def register_executor(self, kind: str, executor: Executor) -> None:
        normalized = str(kind or "").strip()
        if not normalized or not SAFE_JOB_ID.fullmatch(normalized):
            raise ValueError("Upload executor kind must be a safe non-empty key.")
        if not callable(executor):
            raise ValueError("Upload executor must be callable.")
        self._executors[normalized] = executor

    def enqueue(
        self,
        *,
        kind: str,
        study_id: str,
        participant_id: str,
        session_id: str,
        label: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
        created_epoch: float | None = None,
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip()
        if not normalized_kind or not SAFE_JOB_ID.fullmatch(normalized_kind):
            raise UploadJobError(f"Unsupported upload kind: {normalized_kind or '(empty)'}")
        normalized_job_id = str(job_id or uuid.uuid4()).strip()
        if not SAFE_JOB_ID.fullmatch(normalized_job_id):
            raise UploadJobError("Upload job ID is invalid.")
        now = float(created_epoch if created_epoch is not None else self._clock())

        with self._lock:
            existing = self._jobs.get(normalized_job_id)
            if existing is not None:
                return _public_job(existing)

            payload_path = self.payload_dir / f"{normalized_job_id}.json"
            atomic_write_json(payload_path, payload)
            job = {
                "job_id": normalized_job_id,
                "kind": normalized_kind,
                "study_id": str(study_id or "").strip(),
                "participant_id": str(participant_id or "").strip(),
                "session_id": str(session_id or "").strip() or normalized_job_id,
                "label": str(label or normalized_kind.title()).strip(),
                "created_at": _iso_time(now),
                "created_epoch": now,
                "attempts": 0,
                "next_attempt_at": _iso_time(now),
                "next_attempt_epoch": now,
                "status": "queued",
                "last_error": "",
                "steps": [
                    {"key": "local_save", "status": "done"},
                    {"key": normalized_kind, "status": "queued"},
                ],
                "metadata": dict(metadata or {}),
            }
            self._append_event({"event": "created", "job": job})
            self._jobs[normalized_job_id] = job
        self._wake.set()
        return _public_job(job)

    def migrate_legacy_notion_queue(self) -> dict[str, int | str]:
        """Idempotently move the old Notion JSONL queue into journaled jobs."""
        path = self.legacy_notion_queue_path
        if not path.exists():
            return {"found": 0, "migrated": 0}
        try:
            entries = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as error:
            return {"found": 0, "migrated": 0, "error": str(error)}
        if any(not isinstance(entry, dict) for entry in entries):
            return {
                "found": len(entries),
                "migrated": 0,
                "error": "Legacy Notion queue contains an invalid entry; the original file was kept.",
            }

        migrated = 0
        for entry in entries:
            canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")
            deterministic_id = f"legacy-notion-{hashlib.sha256(canonical).hexdigest()[:24]}"
            result_payload = entry.get("result_payload") or {}
            saved_output = entry.get("saved_output") or {}
            created_epoch = _parse_epoch(entry.get("queued_at")) or self._clock()
            sanitized_entry = _redact_legacy_payload(entry)
            self.enqueue(
                kind="notion",
                study_id=str(result_payload.get("study_id") or ""),
                participant_id=str(result_payload.get("participant_id") or ""),
                session_id=str(result_payload.get("session_id") or Path(str(saved_output.get("json_file") or "")).stem),
                label="Notion",
                payload=sanitized_entry,
                metadata=_job_metadata(result_payload, saved_output),
                job_id=deterministic_id,
                created_epoch=created_epoch,
            )
            migrated += 1

        try:
            path.unlink()
        except OSError as error:
            return {"found": len(entries), "migrated": migrated, "error": str(error)}
        return {"found": len(entries), "migrated": migrated}

    def process_due_jobs_once(self, *, limit: int | None = None) -> int:
        now = self._clock()
        with self._lock:
            due_ids = [
                job_id
                for job_id, job in sorted(
                    self._jobs.items(),
                    key=lambda item: (item[1].get("next_attempt_epoch", 0), item[1].get("created_epoch", 0)),
                )
                if job.get("status") == "queued"
                and float(job.get("next_attempt_epoch") or 0) <= now
            ]
        if limit is not None:
            due_ids = due_ids[: max(0, int(limit))]
        for job_id in due_ids:
            self._run_job(job_id)
        return len(due_ids)

    def retry(self, *, job_id: str = "", all_failed: bool = False, kind: str = "") -> dict[str, Any]:
        with self._lock:
            if all_failed:
                targets = [
                    job
                    for job in self._jobs.values()
                    if job.get("status") == "failed" and (not kind or job.get("kind") == kind)
                ]
            else:
                job = self._jobs.get(str(job_id or "").strip())
                if job is None:
                    raise UploadJobError("Upload job was not found.")
                if kind and job.get("kind") != kind:
                    raise UploadJobError("Upload job does not match the requested destination.")
                if job.get("status") == "done":
                    raise UploadJobError("A completed upload cannot be retried.")
                targets = [job]

            now = self._clock()
            retried = []
            for job in targets:
                if job.get("status") == "running":
                    continue
                event = {
                    "event": "retry_requested",
                    "job_id": job["job_id"],
                    "next_attempt_epoch": now,
                    "next_attempt_at": _iso_time(now),
                }
                self._append_event(event)
                self._apply_event(event)
                retried.append(job["job_id"])
        self._wake.set()
        return {"ok": True, "retried": len(retried), "job_ids": retried}

    def status(self, *, days: int = DEFAULT_STATUS_DAYS) -> dict[str, Any]:
        bounded_days = min(MAX_STATUS_DAYS, max(1, int(days)))
        cutoff = self._clock() - bounded_days * 24 * 60 * 60
        with self._lock:
            jobs = [
                _public_job(job)
                for job in self._jobs.values()
                if float(job.get("created_epoch") or 0) >= cutoff
            ]
        jobs.sort(key=lambda job: (job["created_epoch"], job["job_id"]), reverse=True)

        sessions: dict[str, dict[str, Any]] = {}
        for job in jobs:
            session_key = job["session_id"] or job["job_id"]
            session = sessions.setdefault(
                session_key,
                {
                    "session_id": session_key,
                    "study_id": job["study_id"],
                    "participant_id": job["participant_id"],
                    "created_at": job["created_at"],
                    "metadata": job.get("metadata") or {},
                    "jobs": [],
                },
            )
            session["jobs"].append(job)
        return {"ok": True, "days": bounded_days, "sessions": list(sessions.values())}

    def counts(self, *, kind: str = "") -> dict[str, int]:
        with self._lock:
            matching = [
                job
                for job in self._jobs.values()
                if not kind or job.get("kind") == kind
            ]
        return {
            "queued": sum(job.get("status") == "queued" for job in matching),
            "running": sum(job.get("status") == "running" for job in matching),
            "done": sum(job.get("status") == "done" for job in matching),
            "failed": sum(job.get("status") == "failed" for job in matching),
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="study-runner-upload-jobs",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        self._thread = None

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            processed = self.process_due_jobs_once()
            if processed:
                continue
            self._wake.wait(timeout=self._seconds_until_next_job())
            self._wake.clear()

    def _seconds_until_next_job(self) -> float:
        now = self._clock()
        with self._lock:
            due = [
                float(job.get("next_attempt_epoch") or now)
                for job in self._jobs.values()
                if job.get("status") == "queued"
            ]
        return max(0.05, min(5.0, min(due) - now)) if due else 5.0

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.get("status") != "queued":
                return
            attempts = int(job.get("attempts") or 0) + 1
            attempt_event = {"event": "attempt", "job_id": job_id, "attempts": attempts}
            self._append_event(attempt_event)
            self._apply_event(attempt_event)
            payload_path = self.payload_dir / f"{job_id}.json"
            kind = str(job.get("kind") or "")

        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            executor = self._executors.get(kind)
            if executor is None:
                raise UploadJobError(f"No executor is registered for {kind}.")
            result = executor(payload) or {"ok": True}
            if result.get("ok") is False:
                raise UploadJobError(str(result.get("error") or f"{kind} upload failed."))
        except Exception as error:
            self._record_failure(job_id, str(error))
            return

        with self._lock:
            done_event = {
                "event": "done",
                "job_id": job_id,
                "completed_at": _iso_time(self._clock()),
                "result": _safe_result(result),
            }
            self._append_event(done_event)
            self._apply_event(done_event)
        try:
            payload_path.unlink(missing_ok=True)
        except OSError as error:
            print(f"[UPLOADS] Could not remove completed job payload {payload_path.name}: {error}")

    def _record_failure(self, job_id: str, error_message: str) -> None:
        now = self._clock()
        with self._lock:
            job = self._jobs[job_id]
            attempts = int(job.get("attempts") or 1)
            created_epoch = job.get("created_epoch")
            if not isinstance(created_epoch, (int, float)):
                created_epoch = now
            expired = now - float(created_epoch) >= MAX_RETRY_AGE_SECONDS
            if expired:
                event = {
                    "event": "failed",
                    "job_id": job_id,
                    "failed_at": _iso_time(now),
                    "last_error": error_message,
                }
            else:
                next_epoch = now + retry_delay_seconds(attempts)
                event = {
                    "event": "retry_scheduled",
                    "job_id": job_id,
                    "next_attempt_epoch": next_epoch,
                    "next_attempt_at": _iso_time(next_epoch),
                    "last_error": error_message,
                }
            self._append_event(event)
            self._apply_event(event)

    def _append_event(self, event: dict[str, Any]) -> None:
        persisted = {
            **event,
            "at": event.get("at") or _iso_time(self._clock()),
            "at_epoch": event.get("at_epoch") or self._clock(),
        }
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8", newline="\n") as file_handle:
            file_handle.write(json.dumps(persisted, ensure_ascii=False, separators=(",", ":")) + "\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())

    def _replay_journal(self) -> None:
        if not self.journal_path.exists():
            return
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            print(f"[UPLOADS] Could not read job journal: {error}")
            return
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                self._apply_event(event)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                print(f"[UPLOADS] Ignoring invalid journal event on line {line_number}: {error}")

    def _recover_interrupted_attempts(self) -> None:
        now = self._clock()
        interrupted = [
            job["job_id"]
            for job in self._jobs.values()
            if job.get("status") == "running"
        ]
        for job_id in interrupted:
            event = {
                "event": "retry_scheduled",
                "job_id": job_id,
                "next_attempt_epoch": now,
                "next_attempt_at": _iso_time(now),
                "last_error": "Server restarted during the previous upload attempt.",
            }
            self._append_event(event)
            self._apply_event(event)

    def _apply_event(self, event: dict[str, Any]) -> None:
        event_type = event["event"]
        if event_type == "created":
            job = dict(event["job"])
            if not SAFE_JOB_ID.fullmatch(str(job.get("job_id") or "")):
                raise ValueError("invalid job_id")
            self._jobs[job["job_id"]] = job
            return
        job = self._jobs.get(str(event.get("job_id") or ""))
        if job is None:
            return
        destination_step = next(
            (step for step in job.get("steps", []) if step.get("key") == job.get("kind")),
            None,
        )
        if event_type == "attempt":
            job.update(status="running", attempts=int(event["attempts"]), last_error="")
            if destination_step:
                destination_step["status"] = "running"
        elif event_type in {"retry_scheduled", "retry_requested"}:
            job.update(
                status="queued",
                next_attempt_epoch=float(event["next_attempt_epoch"]),
                next_attempt_at=event["next_attempt_at"],
                last_error=str(event.get("last_error") or ""),
            )
            if destination_step:
                destination_step["status"] = "queued"
        elif event_type == "done":
            job.update(
                status="done",
                completed_at=event["completed_at"],
                last_error="",
                result=event.get("result") or {},
            )
            if destination_step:
                destination_step["status"] = "done"
        elif event_type == "failed":
            job.update(
                status="failed",
                failed_at=event["failed_at"],
                last_error=str(event.get("last_error") or ""),
            )
            if destination_step:
                destination_step["status"] = "failed"


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "job_id",
        "kind",
        "study_id",
        "participant_id",
        "session_id",
        "label",
        "created_at",
        "created_epoch",
        "attempts",
        "next_attempt_at",
        "status",
        "last_error",
        "steps",
        "metadata",
        "completed_at",
        "failed_at",
        "result",
    )
    return {key: job[key] for key in allowed if key in job}


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key in {"ok", "uploaded", "endpoint", "message"}
    }


def _job_metadata(result_payload: dict[str, Any], saved_output: dict[str, Any]) -> dict[str, Any]:
    files = [
        value
        for key, value in saved_output.items()
        if key.endswith("_file") and isinstance(value, str) and value
    ]
    return {
        "answer_count": len(result_payload.get("answer_details") or result_payload.get("answers") or {}),
        "recorded_files": files,
    }


def build_job_metadata(result_payload: dict[str, Any], saved_output: dict[str, Any]) -> dict[str, Any]:
    return _job_metadata(result_payload, saved_output)


def _iso_time(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_epoch(value: Any) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _redact_legacy_payload(entry: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(entry)
    hardware_config = payload.get("hardware_config")
    if not isinstance(hardware_config, dict):
        return payload
    notion_config = hardware_config.get("notion")
    if isinstance(notion_config, dict):
        notion_config.pop("api_key", None)
    nextcloud_config = hardware_config.get("nextcloud")
    if isinstance(nextcloud_config, dict):
        nextcloud_config.pop("password", None)
    return payload
