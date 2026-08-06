"""Crash-safe, idempotent study-session finalization.

``FinalizationService`` owns the durable state transitions after participant
submission.  It does not write XDF bytes and it does not call Notion or
Nextcloud clients directly. Recording and destination work enter through
small injectable contracts, keeping this module replayable and independently
testable.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Protocol

from study_runner.recording.artifacts import ArtifactPaths, ArtifactStore, SessionIdentity

from .artifact_manifest_service import ArtifactManifestError, ArtifactManifestStore
from study_runner.shared.atomic_io import atomic_write_json
from ..studies.card_summary_service import CardSummaryBuilder
from .destination_plugin_service import (
    DestinationPluginDefinition,
    definitions_from_state,
    installed_destination_definitions,
    validate_destination_definitions,
)
from ..studies.results_service import sanitize_canonical_submission_sensor_summaries


FINALIZATION_SCHEMA = "study-runner/finalization-state/v1"
SUBMISSION_COMMIT_SCHEMA = "study-runner/submission-commit/v1"
PUBLIC_STATUSES = {"queued", "running", "attention_required", "completed", "completed_degraded"}
STEP_STATUSES = {"pending", "running", "retrying", "done", "failed", "skipped"}
CORE_STEPS = (
    "freeze_recording",
    "validate_sources",
    "merge_xdf",
    "validate_merge",
    "build_card_summary",
    "write_result_manifest",
)
FINAL_STEPS = ("purge_local_sources",)
STEP_KEYS = ("commit_submission",) + CORE_STEPS + FINAL_STEPS
STEP_LABELS = {
    "commit_submission": "Submission lokal sichern",
    "freeze_recording": "Aufnahme schließen",
    "validate_sources": "Quelldaten prüfen",
    "merge_xdf": "XDF-Dateien zusammenführen",
    "validate_merge": "Merge-Parität prüfen",
    "build_card_summary": "Card-Statistiken berechnen",
    "write_result_manifest": "Ergebnis und Manifest schreiben",
    "purge_local_sources": "Lokale Quelldateien freigeben",
}


class FinalizationError(RuntimeError):
    pass


class FinalizationNotFoundError(FinalizationError):
    pass


class SubmissionConflictError(FinalizationError):
    pass


class InvalidTransitionError(FinalizationError):
    pass


class RetryableStepError(FinalizationError):
    def __init__(self, message: str, *, retry_after_seconds: float = 5.0) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.05, float(retry_after_seconds))


class DeferredStep(RetryableStepError):
    """The external operation is journaled/running, rather than failed."""


@dataclass(frozen=True)
class StepResult:
    status: str = "done"
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"done", "skipped"}:
            raise ValueError("StepResult status must be done or skipped.")


@dataclass(frozen=True)
class FinalizationContext:
    paths: ArtifactPaths
    state: dict[str, Any]
    submission: dict[str, Any]
    config_data: dict[str, Any]
    hardware_config: dict[str, Any]

    @property
    def recording_expected(self) -> bool:
        return bool((self.state.get("context") or {}).get("recording_expected"))


class RecordingFinalizationAdapter(Protocol):
    def freeze(self, context: FinalizationContext) -> StepResult | Mapping[str, Any] | None:
        ...

    def validate_sources(self, context: FinalizationContext) -> StepResult | Mapping[str, Any] | None:
        ...

    def merge(self, context: FinalizationContext) -> StepResult | Mapping[str, Any] | None:
        ...

    def validate_merge(self, context: FinalizationContext) -> StepResult | Mapping[str, Any] | None:
        ...


class DestinationHandler(Protocol):
    def publish(self, destination: str, context: FinalizationContext) -> StepResult | Mapping[str, Any] | None:
        ...

    def retry(self, destination: str, context: FinalizationContext) -> None:
        """Requeue an underlying persistent destination job, if one exists."""
        ...


class NullRecordingFinalizationAdapter:
    """Safe default for studies which did not select a recording plugin."""

    def _result(self, context: FinalizationContext) -> StepResult:
        if context.recording_expected:
            raise FinalizationError("Recording was required, but no recording finalization adapter is configured.")
        return StepResult("skipped", {"reason": "no_recording_source_selected"})

    def freeze(self, context: FinalizationContext) -> StepResult:
        return self._result(context)

    def validate_sources(self, context: FinalizationContext) -> StepResult:
        return self._result(context)

    def merge(self, context: FinalizationContext) -> StepResult:
        return self._result(context)

    def validate_merge(self, context: FinalizationContext) -> StepResult:
        return self._result(context)


class UploadJobDestinationHandler:
    """Bridge finalization destinations to the existing persistent upload queue."""

    def __init__(self, service: Any, data_dir: Path) -> None:
        self.service = service
        self.data_dir = Path(data_dir).resolve()

    def publish(self, destination: str, context: FinalizationContext) -> StepResult:
        job_id = _destination_job_id(
            context.state["job_id"],
            destination,
            int(context.state.get("publication_generation") or 1),
        )
        existing = self._find_job(job_id)
        if existing is None:
            payload = self._payload(context)
            existing = self.service.enqueue(
                kind=destination,
                study_id=context.state["study_id"],
                participant_id=context.state["participant_id"],
                session_id=context.state["session_id"],
                label=destination.title(),
                payload=payload,
                metadata={
                    "finalization_job_id": context.state["job_id"],
                    "session_folder": context.paths.root.name,
                },
                job_id=job_id,
            )
        status = str(existing.get("status") or "")
        if status == "done":
            upload_result = existing.get("result") or {}
            details = {"upload_job_id": job_id, "result": upload_result}
            if isinstance(upload_result.get("remote_sha256"), dict):
                details["remote_sha256"] = dict(upload_result["remote_sha256"])
            return StepResult("done", details)
        if status == "failed":
            raise FinalizationError(str(existing.get("last_error") or f"{destination} upload failed."))
        raise DeferredStep(
            f"{destination} upload is {status or 'queued'}.",
            retry_after_seconds=1.0,
        )

    def retry(self, destination: str, context: FinalizationContext) -> None:
        """Retry the deterministic upload job behind a finalization step.

        Resetting only the outer state-machine step would immediately find the
        same failed upload job again. The two journals therefore transition
        together while keeping the stable destination job id.
        """

        job_id = _destination_job_id(
            context.state["job_id"],
            destination,
            int(context.state.get("publication_generation") or 1),
        )
        existing = self._find_job(job_id)
        if existing is None:
            return
        status = str(existing.get("status") or "")
        if status in {"done", "running"}:
            return
        try:
            self.service.retry(job_id=job_id, kind=destination)
        except Exception as error:
            raise FinalizationError(
                f"Could not retry the persistent {destination} upload: {error}"
            ) from error

    def _find_job(self, job_id: str) -> dict[str, Any] | None:
        status = self.service.status(days=30)
        for session in status.get("sessions", []):
            for job in session.get("jobs", []):
                if job.get("job_id") == job_id:
                    return job
        return None

    def _payload(self, context: FinalizationContext) -> dict[str, Any]:
        result_path = context.paths.root / "result.json"
        result_payload = _read_json_object(result_path) if result_path.is_file() else dict(context.submission)
        relative_root = context.paths.root.relative_to(self.data_dir).as_posix()
        saved_output = {
            "participant_dir": relative_root,
            "session_dir": relative_root,
            "session_relative_path": relative_root,
            "json_file": f"{relative_root}/result.json",
            "card_summary_file": f"{relative_root}/card-summary.json",
            "manifest_file": f"{relative_root}/manifest.json",
            "xdf_file": (
                f"{relative_root}/derived/session.xdf"
                if context.paths.merged_xdf.is_file()
                else None
            ),
            "card_summary": (
                _read_json_object(context.paths.root / "card-summary.json")
                if (context.paths.root / "card-summary.json").is_file()
                else {}
            ),
        }
        return {
            "result_payload": result_payload,
            "hardware_config": context.hardware_config,
            "saved_output": saved_output,
            "config_data": context.config_data,
        }


class FinalizationService:
    """Persistent state machine with replay, retry, and degraded confirmation."""

    def __init__(
        self,
        data_dir: Path,
        *,
        recording_adapter: RecordingFinalizationAdapter | None = None,
        destination_handler: DestinationHandler | None = None,
        destination_definitions: tuple[DestinationPluginDefinition, ...] | None = None,
        card_summary_builder: CardSummaryBuilder | None = None,
        manifest_store: ArtifactManifestStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.artifacts = ArtifactStore(self.data_dir)
        self.recording_adapter = recording_adapter or NullRecordingFinalizationAdapter()
        self.destination_handler = destination_handler
        self.destination_definitions = validate_destination_definitions(
            destination_definitions
            if destination_definitions is not None
            else installed_destination_definitions()
        )
        self.card_summary_builder = card_summary_builder or CardSummaryBuilder()
        self.manifest_store = manifest_store or ArtifactManifestStore()
        self._clock = clock
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        self._submission_index: dict[str, str] = {}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_committed_jobs()
        self._recover_interrupted_steps()

    def _destinations(
        self,
        state: Mapping[str, Any],
    ) -> tuple[DestinationPluginDefinition, ...]:
        return definitions_from_state(state, self.destination_definitions)

    def _destination_by_step(
        self,
        state: Mapping[str, Any],
        step_key: str,
    ) -> DestinationPluginDefinition | None:
        return next(
            (
                definition
                for definition in self._destinations(state)
                if definition.step_key == step_key
            ),
            None,
        )

    def _destination_step_keys(self, state: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(definition.step_key for definition in self._destinations(state))

    @staticmethod
    def _step_keys(state: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(
            str(step.get("key") or "")
            for step in state.get("steps", [])
            if isinstance(step, Mapping)
        )

    def commit_submission(
        self,
        submission: dict[str, Any],
        *,
        config_data: dict[str, Any] | None = None,
        hardware_config: dict[str, Any] | None = None,
        recording_expected: bool = False,
        started_at_epoch: float | None = None,
    ) -> dict[str, Any]:
        payload = deepcopy(submission)
        payload = sanitize_canonical_submission_sensor_summaries(payload)
        study_id = str(payload.get("study_id") or "").strip()
        participant_id = str(payload.get("participant_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if not study_id or not participant_id or not session_id:
            raise FinalizationError("study_id, participant_id, and session_id are required for finalization.")
        submission_id = str(payload.get("submission_id") or session_id).strip()
        payload["submission_id"] = submission_id
        payload_hash = _canonical_hash(payload)

        with self._lock:
            submission_key = _submission_key(study_id, participant_id, submission_id)
            existing_id = self._submission_index.get(submission_key)
            if existing_id:
                existing = self._jobs[existing_id]
                if existing.get("submission_sha256") != payload_hash:
                    raise SubmissionConflictError("submission_id was already committed with different content.")
                return {**self.public_job(existing), "created": False}

            identity = SessionIdentity(
                study_id=study_id,
                participant_id=participant_id,
                session_id=session_id,
                started_at=(
                    dt.datetime.fromtimestamp(float(started_at_epoch), tz=dt.timezone.utc)
                    if started_at_epoch is not None
                    else _submission_started_at(payload, self._clock())
                ),
            )
            paths = self.artifacts.reserve(identity)
            job_id = _job_id(study_id, participant_id, submission_id)
            now = self._clock()
            settings = (config_data or {}).get("study_settings") or {}
            state = {
                "schema": FINALIZATION_SCHEMA,
                "revision": 1,
                "job_id": job_id,
                "submission_id": submission_id,
                "submission_sha256": payload_hash,
                "study_id": study_id,
                "participant_id": participant_id,
                "session_id": session_id,
                "session_path": paths.root.relative_to(self.data_dir).as_posix(),
                "status": "queued",
                "quality_status": "pending",
                "created_at": _iso_time(now),
                "created_epoch": now,
                "updated_at": _iso_time(now),
                "updated_epoch": now,
                "steps": _initial_steps(settings, self.destination_definitions),
                "destinations": [
                    definition.persisted()
                    for definition in self.destination_definitions
                ],
                "warnings": [],
                "context": {
                    "config_data": deepcopy(config_data or {}),
                    "hardware_config": deepcopy(hardware_config or {}),
                    "recording_expected": bool(recording_expected),
                },
                "runtime": {},
                "publication_generation": 1,
            }
            commit = {
                "schema": SUBMISSION_COMMIT_SCHEMA,
                "submission": payload,
                "state": state,
            }
            # One atomic source-of-truth file commits both objects logically.
            # The two canonical projections are repairable from it on restart.
            atomic_write_json(paths.root / ".submission-commit.json", commit)
            self._jobs[job_id] = state
            self._submission_index[submission_key] = job_id

            # Nothing after the durable commit may turn an accepted
            # submission back into an HTTP error.  These files are projections
            # of ``.submission-commit.json`` and ``_load_committed_jobs`` can
            # recreate them after a crash.  Keeping the in-memory registration
            # first also makes an immediate duplicate submit idempotent.
            projection_errors: list[str] = []
            for projection_path, projection_payload in (
                (paths.root / "submission.json", payload),
                (paths.root / "finalization-state.json", state),
            ):
                try:
                    atomic_write_json(projection_path, projection_payload)
                except Exception as error:
                    projection_errors.append(f"{projection_path.name}: {error}")
            try:
                self._append_log(paths, {"event": "submission_committed", "job_id": job_id})
            except Exception as error:
                projection_errors.append(f"logs/finalization.jsonl: {error}")
            if projection_errors:
                print(
                    "[FINALIZATION] Submission committed; repairable projection writes failed: "
                    + "; ".join(projection_errors)
                )

        self._wake.set()
        return {**self.public_job(state), "created": True}

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._jobs.get(str(job_id or "").strip())
            if state is None:
                raise FinalizationNotFoundError("Finalization job was not found.")
            return self.public_job(state)

    def status(self, *, days: int = 30) -> dict[str, Any]:
        bounded_days = min(365, max(1, int(days)))
        cutoff = self._clock() - bounded_days * 24 * 60 * 60
        with self._lock:
            jobs = [
                self.public_job(state)
                for state in self._jobs.values()
                if float(state.get("created_epoch") or 0) >= cutoff
            ]
        jobs.sort(key=lambda item: (item.get("created_epoch", 0), item["job_id"]), reverse=True)
        counts = {status: sum(item["status"] == status for item in jobs) for status in sorted(PUBLIC_STATUSES)}
        return {"ok": True, "days": bounded_days, "counts": counts, "jobs": jobs}

    def retry(self, job_id: str, *, step_key: str = "") -> dict[str, Any]:
        with self._lock:
            state = self._require_state(job_id)
            target = str(step_key or "").strip()
            failed_keys = [step["key"] for step in state["steps"] if step["status"] in {"failed", "retrying"}]
            if target:
                if target not in self._step_keys(state) or target == "commit_submission":
                    raise InvalidTransitionError("The requested finalization step cannot be retried.")
                if _step(state, target)["status"] not in {"failed", "retrying"}:
                    raise InvalidTransitionError("The requested finalization step is not failed or retrying.")
            elif failed_keys:
                target = failed_keys[0]
            else:
                raise InvalidTransitionError("This finalization has no retryable step.")

            previous_status = str(state.get("status") or "")
            destination_definition = self._destination_by_step(state, target)
            if destination_definition is not None and self.destination_handler is not None:
                retry_destination = getattr(self.destination_handler, "retry", None)
                if callable(retry_destination):
                    retry_destination(
                        destination_definition.destination,
                        self._context(state),
                    )
            self._reset_from(state, target)
            if destination_definition is not None and previous_status in {
                "attention_required",
                "completed_degraded",
            }:
                # A network retry cannot change the already established
                # scientific quality state or revoke an admin's degraded
                # confirmation.
                state["status"] = previous_status
            else:
                state["status"] = "queued"
                if destination_definition is None:
                    state["quality_status"] = "pending"
                    state.pop("degraded_confirmation", None)
            self._persist_state(state, event={"event": "retry_requested", "step": target})
        self._wake.set()
        return self.public_job(state)

    def confirm_degraded(self, job_id: str, *, reason: str, confirmed_by: str = "admin") -> dict[str, Any]:
        explanation = str(reason or "").strip()
        if not explanation:
            raise InvalidTransitionError("A reason is required to confirm degraded completion.")
        with self._lock:
            state = self._require_state(job_id)
            if state.get("status") != "attention_required":
                raise InvalidTransitionError("Only an attention-required finalization can be confirmed degraded.")
            attention_destinations = [
                definition
                for definition in self._destinations(state)
                if definition.publish_on_attention
                and _step(state, definition.step_key).get("enabled", True)
            ]
            unsettled = [
                definition
                for definition in attention_destinations
                if _step(state, definition.step_key).get("status")
                not in {"done", "failed", "skipped"}
            ]
            if unsettled:
                labels = ", ".join(
                    definition.destination.title() for definition in unsettled
                )
                raise InvalidTransitionError(
                    f"Wait for the attention-required {labels} backup to finish or fail "
                    "before confirming degraded completion."
                )
            context = self._context(state)
            self._ensure_degraded_result(context, explanation)
            state["degraded_confirmation"] = {
                "reason": explanation,
                "confirmed_by": str(confirmed_by or "admin"),
                "confirmed_at": _iso_time(self._clock()),
            }
            state["quality_status"] = "degraded"
            state["status"] = "completed_degraded"
            _step(state, "purge_local_sources").update(
                status="skipped",
                details={"reason": "local_sources_are_retained_for_degraded_sessions"},
            )
            for definition in self._destinations(state):
                destination_step = _step(state, definition.step_key)
                if destination_step["status"] == "pending":
                    destination_step.pop("blocked_by", None)
            republish = [
                definition
                for definition in attention_destinations
                if definition.republish_on_degraded
            ]
            if republish:
                # The earlier attention upload is immutable generation 1.
                # Degraded confirmation publishes a new status generation so
                # the remote ATTENTION marker is replaced only after every
                # now-final artifact has been hash-verified.
                state["publication_generation"] = int(
                    state.get("publication_generation") or 1
                ) + 1
                for definition in republish:
                    destination_step = _step(state, definition.step_key)
                    destination_step.clear()
                    destination_step.update(
                        _destination_step(definition, enabled=True)
                    )
            self.manifest_store.publish_marker(
                context.paths,
                status="completed_degraded",
                job_id=state["job_id"],
                details={"reason": explanation},
            )
            self._persist_state(state, event={"event": "degraded_confirmed", "reason": explanation})
        self._wake.set()
        return self.public_job(state)

    def process_due_jobs_once(self, *, limit: int | None = None) -> int:
        now = self._clock()
        with self._lock:
            candidates = [
                state["job_id"]
                for state in sorted(self._jobs.values(), key=lambda value: value.get("created_epoch", 0))
                if self._job_has_due_work(state, now)
            ]
        if limit is not None:
            candidates = candidates[: max(0, int(limit))]
        for job_id in candidates:
            try:
                self._advance(job_id)
            except Exception as error:
                self._quarantine_unhandled_job_error(job_id, error)
        return len(candidates)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker_loop, name="study-runner-finalization", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        self._thread = None

    def public_job(self, state: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "job_id",
            "submission_id",
            "study_id",
            "participant_id",
            "session_id",
            "session_path",
            "status",
            "quality_status",
            "created_at",
            "created_epoch",
            "updated_at",
            "warnings",
            "steps",
            "degraded_confirmation",
        )
        return deepcopy({key: state[key] for key in allowed if key in state})

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.process_due_jobs_once()
            except Exception as error:
                # Last-resort daemon boundary. Individual scientific steps are
                # persisted by _run_step; this guard prevents an unrelated
                # marker/filesystem failure from permanently stopping every
                # other session's finalization.
                print(f"[FINALIZATION] Background worker recovered from an unhandled error: {error}")
                self._stop.wait(timeout=1.0)
                continue
            if processed:
                continue
            self._wake.wait(timeout=1.0)
            self._wake.clear()

    def _quarantine_unhandled_job_error(self, job_id: str, error: Exception) -> None:
        """Best-effort isolation for failures outside normal step handlers."""

        message = f"internal_finalization: {type(error).__name__}: {error}"
        print(f"[FINALIZATION] {job_id}: {message}")
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return
            state["status"] = "attention_required"
            state["quality_status"] = "invalid"
            if message not in state["warnings"]:
                state["warnings"].append(message)
            try:
                context = self._context(state)
                self.manifest_store.publish_marker(
                    context.paths,
                    status="attention_required",
                    job_id=job_id,
                    details={"failed_step": "internal_finalization", "error": str(error)},
                )
                self._persist_state(
                    state,
                    event={"event": "unhandled_job_error", "error": str(error)},
                )
            except Exception as persistence_error:
                # Keeping the in-memory quarantine still prevents a tight
                # retry loop. A later process restart will replay the last
                # durable state and try persistence again.
                print(
                    f"[FINALIZATION] Could not persist quarantine for {job_id}: "
                    f"{persistence_error}"
                )

    def _advance(self, job_id: str) -> None:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return
            if state.get("status") == "attention_required":
                self._advance_attention_upload(state)
                return
            if state.get("status") == "completed_degraded":
                self._advance_destinations(state, degraded=True)
                return
            if state.get("status") == "completed":
                return
            state["status"] = "running"
            self._persist_state(state, event={"event": "finalization_running"})

            for step_key in CORE_STEPS:
                step = _step(state, step_key)
                if step["status"] in {"done", "skipped"}:
                    continue
                if step["status"] == "retrying" and float(step.get("next_attempt_epoch") or 0) > self._clock():
                    return
                if not self._run_step(state, step_key):
                    return

            # The scientific artifact set is now immutable. Publish the
            # completion marker before network work so Nextcloud can upload it
            # strictly last; the public job remains ``running`` until every
            # enabled destination has independently finished.
            if not state["runtime"].get("local_completion_published"):
                context = self._context(state)
                self.manifest_store.publish_marker(
                    context.paths,
                    status="completed",
                    job_id=state["job_id"],
                    details={
                        "quality_status": "valid",
                        "scope": "scientific_local_commit",
                    },
                )
                state["runtime"]["local_completion_published"] = True
                state["quality_status"] = "valid"
                self._persist_state(state, event={"event": "local_completion_published"})

            if not self._advance_destinations(state, degraded=False):
                return
            if not self._run_purge(state):
                return
            state["status"] = "completed"
            state["quality_status"] = "valid"
            self._persist_state(state, event={"event": "finalization_completed"})

    def _run_step(self, state: dict[str, Any], step_key: str) -> bool:
        step = _step(state, step_key)
        step.update(
            status="running",
            attempts=int(step.get("attempts") or 0) + 1,
            started_at=_iso_time(self._clock()),
            last_error="",
        )
        self._persist_state(state, event={"event": "step_started", "step": step_key})
        context = self._context(state)
        try:
            outcome = self._execute_step(step_key, context)
        except RetryableStepError as error:
            step.update(
                status="retrying",
                last_error=str(error),
                next_attempt_epoch=self._clock() + error.retry_after_seconds,
                next_attempt_at=_iso_time(self._clock() + error.retry_after_seconds),
            )
            self._persist_state(state, event={"event": "step_retrying", "step": step_key, "error": str(error)})
            return False
        except Exception as error:
            step.update(status="failed", last_error=str(error), failed_at=_iso_time(self._clock()))
            self._enter_attention(state, step_key, str(error))
            return False

        result = _coerce_step_result(outcome)
        step.update(
            status=result.status,
            details=deepcopy(result.details or {}),
            completed_at=_iso_time(self._clock()),
        )
        step.pop("next_attempt_epoch", None)
        step.pop("next_attempt_at", None)
        self._persist_state(state, event={"event": "step_completed", "step": step_key, "status": result.status})
        return True

    def _execute_step(self, step_key: str, context: FinalizationContext) -> StepResult | Mapping[str, Any] | None:
        if step_key == "freeze_recording":
            return self.recording_adapter.freeze(context)
        if step_key == "validate_sources":
            return self.recording_adapter.validate_sources(context)
        if step_key == "merge_xdf":
            return self.recording_adapter.merge(context)
        if step_key == "validate_merge":
            outcome = self.recording_adapter.validate_merge(context)
            result = _coerce_step_result(outcome)
            context.state["runtime"]["merge_parity"] = result.status == "done"
            return result
        if step_key == "build_card_summary":
            return self._build_card_summary(context)
        if step_key == "write_result_manifest":
            return self._write_result_manifest(context)
        raise FinalizationError(f"Unsupported finalization step: {step_key}")

    def _build_card_summary(self, context: FinalizationContext) -> StepResult:
        target = context.paths.root / "card-summary.json"
        if not context.paths.merged_xdf.is_file():
            if context.recording_expected:
                raise FinalizationError("Validated merged XDF is missing.")
            summary = {
                "schema": "study-runner/card-summary/v1",
                "session_id": context.state["session_id"],
                "source": None,
                "window_semantics": "half_open_[start,end)",
                "card_count": 0,
                "stream_count": 0,
                "cards": [],
                "reason": "no_recording_source_selected",
            }
            atomic_write_json(target, summary)
            return StepResult("skipped", {"card_count": 0, "stream_count": 0})
        summary = self.card_summary_builder.build(
            context.paths.merged_xdf,
            context.submission.get("card_events") or [],
            session_id=context.state["session_id"],
            client_clock_offset_ms=context.submission.get("client_clock_offset_ms"),
            require_xdf_markers=context.recording_expected,
            required_marker_event_ids=[
                str((context.submission.get("study_end_event") or {}).get("event_id") or "")
            ],
        )
        for warning in summary.get("quality_warnings") or []:
            if isinstance(warning, Mapping):
                rendered = f"{warning.get('code')}: {warning.get('event_id') or warning.get('message') or ''}".rstrip(": ")
            else:
                rendered = str(warning)
            if rendered and rendered not in context.state["warnings"]:
                context.state["warnings"].append(rendered)
        atomic_write_json(target, summary)
        return StepResult("done", {"card_count": summary["card_count"], "stream_count": summary["stream_count"]})

    def _write_result_manifest(self, context: FinalizationContext) -> StepResult:
        result = {
            **context.submission,
            "server_finalization": {
                "job_id": context.state["job_id"],
                "status": "completed_local",
                "quality_status": "valid" if context.state["runtime"].get("merge_parity") else "not_applicable",
                "card_summary_file": "card-summary.json",
            },
        }
        atomic_write_json(context.paths.root / "result.json", result)
        manifest = self.manifest_store.write(
            context.paths,
            identity=context.paths.identity,
            quality_status="valid" if context.state["runtime"].get("merge_parity") else "not_applicable",
            merge_parity=bool(context.state["runtime"].get("merge_parity")),
            provenance={
                "finalization_schema": FINALIZATION_SCHEMA,
                "card_summary_schema": "study-runner/card-summary/v1",
                "native_rates_preserved": bool(context.state["runtime"].get("merge_parity")),
            },
            warnings=context.state.get("warnings") or [],
        )
        return StepResult("done", {"artifact_count": len(manifest["artifacts"])})

    def _advance_destinations(self, state: dict[str, Any], *, degraded: bool) -> bool:
        all_terminal = True
        definitions = self._destinations(state)
        for definition in definitions:
            step_key = definition.step_key
            step = _step(state, step_key)
            if step["status"] in {"done", "skipped"}:
                continue
            if step["status"] == "failed":
                all_terminal = False
                continue
            if definition.requires_valid_result and not degraded and any(
                _step(state, key)["status"] == "failed" for key in CORE_STEPS
            ):
                step["blocked_by"] = "core_finalization"
                all_terminal = False
                continue
            if step["status"] == "retrying" and float(step.get("next_attempt_epoch") or 0) > self._clock():
                all_terminal = False
                continue
            if self.destination_handler is None:
                step.update(status="skipped", details={"reason": "no_destination_handler_configured"})
                self._persist_state(state, event={"event": "step_skipped", "step": step_key})
                continue
            if not self._run_destination_step(
                state,
                step_key,
                definition.destination,
            ):
                all_terminal = False
        return all_terminal and all(
            _step(state, definition.step_key)["status"] in {"done", "skipped"}
            for definition in definitions
        )

    def _run_destination_step(self, state: dict[str, Any], step_key: str, destination: str) -> bool:
        step = _step(state, step_key)
        step.update(status="running", attempts=int(step.get("attempts") or 0) + 1, last_error="")
        self._persist_state(state, event={"event": "step_started", "step": step_key})
        try:
            outcome = self.destination_handler.publish(destination, self._context(state))  # type: ignore[union-attr]
        except RetryableStepError as error:
            deadline = self._clock() + error.retry_after_seconds
            step.update(
                status="retrying",
                last_error=str(error),
                next_attempt_epoch=deadline,
                next_attempt_at=_iso_time(deadline),
            )
            self._persist_state(state, event={"event": "step_retrying", "step": step_key, "error": str(error)})
            return False
        except Exception as error:
            step.update(status="failed", last_error=str(error), failed_at=_iso_time(self._clock()))
            warning = f"{step_key}: {error}"
            if warning not in state["warnings"]:
                state["warnings"].append(warning)
            # Destination availability is operational, not scientific data
            # quality. Keep an independently retryable failed destination;
            # the already validated local dataset remains valid.
            self._persist_state(
                state,
                event={"event": "destination_failed", "step": step_key, "error": str(error)},
            )
            return False
        result = _coerce_step_result(outcome)
        step.update(status=result.status, details=deepcopy(result.details or {}), completed_at=_iso_time(self._clock()))
        step.pop("next_attempt_epoch", None)
        self._persist_state(state, event={"event": "step_completed", "step": step_key, "status": result.status})
        return True

    def _advance_attention_upload(self, state: dict[str, Any]) -> None:
        # Recovery destinations may publish raw artifacts and the attention
        # marker even when scientific derivation failed. Others remain pending
        # until an operator explicitly confirms degraded completion.
        for definition in self._destinations(state):
            if not definition.publish_on_attention:
                continue
            destination_step = _step(state, definition.step_key)
            if destination_step["status"] in {"done", "skipped", "failed"}:
                continue
            if (
                destination_step["status"] == "retrying"
                and float(destination_step.get("next_attempt_epoch") or 0)
                > self._clock()
            ):
                continue
            if self.destination_handler is None:
                destination_step.update(
                    status="skipped",
                    details={"reason": "no_destination_handler_configured"},
                )
                self._persist_state(
                    state,
                    event={"event": "step_skipped", "step": definition.step_key},
                )
                continue
            self._run_destination_step(
                state,
                definition.step_key,
                definition.destination,
            )

    def _run_purge(self, state: dict[str, Any]) -> bool:
        step = _step(state, "purge_local_sources")
        if step["status"] in {"done", "skipped"}:
            return True
        purge_destinations = [
            definition
            for definition in self._destinations(state)
            if definition.purge_verified_sources
            and _step(state, definition.step_key).get("enabled", True)
        ]
        if not purge_destinations:
            step.update(
                status="skipped",
                details={"reason": "no_verified_source_purge_destination_enabled"},
            )
            self._persist_state(state, event={"event": "step_skipped", "step": step["key"]})
            return True
        remote_hashes = next(
            (
                (_step(state, definition.step_key).get("details") or {}).get(
                    "remote_sha256"
                )
                for definition in purge_destinations
                if isinstance(
                    (_step(state, definition.step_key).get("details") or {}).get(
                        "remote_sha256"
                    ),
                    dict,
                )
            ),
            None,
        )
        if not isinstance(remote_hashes, dict):
            step.update(status="skipped", details={"reason": "remote_source_checksums_not_available; local_sources_retained"})
            self._persist_state(state, event={"event": "step_skipped", "step": step["key"]})
            return True
        step.update(
            status="running",
            attempts=int(step.get("attempts") or 0) + 1,
            started_at=_iso_time(self._clock()),
            last_error="",
        )
        self._persist_state(state, event={"event": "step_started", "step": step["key"]})
        try:
            result = self.manifest_store.purge_plugin_xdfs(
                self._context(state).paths,
                remote_sha256=remote_hashes,
                session_status="completed",
                merge_parity=bool(state["runtime"].get("merge_parity")),
            )
        except ArtifactManifestError as error:
            step.update(status="skipped", details={"reason": str(error), "local_sources_retained": True})
        except Exception as error:
            # Source purge is operational cleanup after a valid immutable
            # dataset exists. Never let an unlink/fsync failure kill the
            # finalization worker thread. Retry a bounded number of times,
            # then retain whatever local sources remain with a visible note.
            attempts = int(step.get("attempts") or 1)
            if attempts < 3:
                retry_at = self._clock() + 5.0
                step.update(
                    status="retrying",
                    last_error=str(error),
                    next_attempt_epoch=retry_at,
                    next_attempt_at=_iso_time(retry_at),
                )
                self._persist_state(
                    state,
                    event={"event": "step_retrying", "step": step["key"], "error": str(error)},
                )
                return False
            step.update(
                status="skipped",
                last_error=str(error),
                details={
                    "reason": f"source purge failed after {attempts} attempts: {error}",
                    "local_sources_retained": True,
                },
            )
        else:
            step.update(status="done", details=result, completed_at=_iso_time(self._clock()))
        self._persist_state(state, event={"event": "step_completed", "step": step["key"], "status": step["status"]})
        return True

    def _enter_attention(self, state: dict[str, Any], step_key: str, error: str) -> None:
        state["status"] = "attention_required"
        state["quality_status"] = "invalid"
        warning = f"{step_key}: {error}"
        if warning not in state["warnings"]:
            state["warnings"].append(warning)
        context = self._context(state)
        try:
            self.manifest_store.write(
                context.paths,
                identity=context.paths.identity,
                quality_status="attention_required",
                merge_parity=bool(state["runtime"].get("merge_parity")),
                provenance={"failed_step": step_key, "finalization_schema": FINALIZATION_SCHEMA},
                warnings=state["warnings"],
            )
            self.manifest_store.publish_marker(
                context.paths,
                status="attention_required",
                job_id=state["job_id"],
                details={"failed_step": step_key, "error": error},
            )
        finally:
            self._persist_state(state, event={"event": "attention_required", "step": step_key, "error": error})

    def _ensure_degraded_result(self, context: FinalizationContext, reason: str) -> None:
        summary_path = context.paths.root / "card-summary.json"
        if not summary_path.is_file():
            atomic_write_json(
                summary_path,
                {
                    "schema": "study-runner/card-summary/v1",
                    "session_id": context.state["session_id"],
                    "source": None,
                    "window_semantics": "half_open_[start,end)",
                    "cards": [],
                    "card_count": 0,
                    "stream_count": 0,
                    "quality_warning": reason,
                },
            )
        atomic_write_json(
            context.paths.root / "result.json",
            {
                **context.submission,
                "server_finalization": {
                    "job_id": context.state["job_id"],
                    "status": "completed_degraded",
                    "quality_status": "degraded",
                    "quality_warning": reason,
                    "card_summary_file": "card-summary.json",
                },
            },
        )
        self.manifest_store.write(
            context.paths,
            identity=context.paths.identity,
            quality_status="degraded",
            merge_parity=bool(context.state["runtime"].get("merge_parity")),
            provenance={"finalization_schema": FINALIZATION_SCHEMA},
            warnings=[*context.state.get("warnings", []), reason],
        )

    def _reset_from(self, state: dict[str, Any], target: str) -> None:
        target_destination = self._destination_by_step(state, target)
        if target_destination is not None:
            # Destination retries are independent: retrying one publication
            # must never invalidate a verified sibling destination.
            reset_keys = (target,)
        else:
            all_keys = self._step_keys(state)
            start = all_keys.index(target)
            reset_keys = all_keys[start:]
        for key in reset_keys:
            if key == "commit_submission":
                continue
            step = _step(state, key)
            enabled = step.get("enabled", True)
            definition = self._destination_by_step(state, key)
            step.clear()
            if definition is not None:
                step.update(_destination_step(definition, enabled=bool(enabled)))
            else:
                step.update(_ordinary_step(key, enabled=bool(enabled)))
        if target_destination is not None:
            state["warnings"] = [
                warning
                for warning in state.get("warnings", [])
                if not str(warning).startswith(f"{target}:")
            ]
            return

        state["warnings"] = []
        runtime = state.setdefault("runtime", {})
        runtime.pop("local_completion_published", None)
        if target in CORE_STEPS and CORE_STEPS.index(target) <= CORE_STEPS.index("validate_merge"):
            runtime.pop("merge_parity", None)

    def _context(self, state: dict[str, Any]) -> FinalizationContext:
        commit_path = self.data_dir / state["session_path"] / ".submission-commit.json"
        commit = _read_json_object(commit_path)
        identity_data = _read_json_object(commit_path.parent / "session-identity.json")
        identity = SessionIdentity(
            study_id=str(identity_data["study_id"]),
            participant_id=str(identity_data["participant_id"]),
            session_id=str(identity_data["session_id"]),
            started_at=_parse_datetime(identity_data["started_at"]),
        )
        bound_root = (self.data_dir / state["session_path"]).resolve()
        if (
            not bound_root.is_relative_to(self.data_dir.resolve())
            or bound_root != commit_path.parent.resolve()
        ):
            raise FinalizationError(
                "Finalization state path does not match its immutable session identity."
            )
        # Reopen the directory to which session-identity.json was bound rather
        # than recomputing a slug. This keeps in-flight sessions readable
        # across future path-sanitizer upgrades while still failing closed on
        # state/path tampering.
        paths = ArtifactPaths(root=bound_root, identity=identity)
        private = state.get("context") or {}
        return FinalizationContext(
            paths=paths,
            state=state,
            submission=deepcopy(commit["submission"]),
            config_data=deepcopy(private.get("config_data") or {}),
            hardware_config=deepcopy(private.get("hardware_config") or {}),
        )

    def _persist_state(self, state: dict[str, Any], *, event: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision") or 0) + 1
        state["updated_epoch"] = self._clock()
        state["updated_at"] = _iso_time(state["updated_epoch"])
        paths = self.data_dir / state["session_path"]
        atomic_write_json(paths / "finalization-state.json", state)
        self._append_log_path(paths, {**event, "job_id": state["job_id"]})

    def _append_log(self, paths: ArtifactPaths, event: dict[str, Any]) -> None:
        self._append_log_path(paths.root, event)

    def _append_log_path(self, root: Path, event: dict[str, Any]) -> None:
        path = root / "logs" / "finalization.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        persisted = {**event, "at": _iso_time(self._clock()), "at_epoch": self._clock()}
        encoded = json.dumps(persisted, ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()

    def _load_committed_jobs(self) -> None:
        for commit_path in self.data_dir.rglob(".submission-commit.json"):
            try:
                commit = _read_json_object(commit_path)
                if commit.get("schema") != SUBMISSION_COMMIT_SCHEMA:
                    continue
                state_path = commit_path.parent / "finalization-state.json"
                submission_path = commit_path.parent / "submission.json"
                state = _read_json_object(state_path) if state_path.is_file() else deepcopy(commit["state"])
                if state.get("schema") != FINALIZATION_SCHEMA or state.get("job_id") in self._jobs:
                    continue
                if not submission_path.is_file():
                    atomic_write_json(submission_path, commit["submission"])
                if not state_path.is_file():
                    atomic_write_json(state_path, state)
                self._jobs[state["job_id"]] = state
                self._submission_index[
                    _submission_key(state["study_id"], state["participant_id"], state["submission_id"])
                ] = state["job_id"]
            except (OSError, ValueError, KeyError, TypeError) as error:
                print(f"[FINALIZATION] Ignoring unreadable commit {commit_path}: {error}")

    def _recover_interrupted_steps(self) -> None:
        for state in self._jobs.values():
            changed = False
            for step in state.get("steps", []):
                if step.get("status") == "running":
                    step["status"] = "retrying"
                    step["last_error"] = "Server restarted during this step; replay scheduled."
                    step["next_attempt_epoch"] = self._clock()
                    step["next_attempt_at"] = _iso_time(self._clock())
                    changed = True
            if state.get("status") == "running":
                state["status"] = "queued"
                changed = True
            if changed:
                self._persist_state(state, event={"event": "interrupted_steps_recovered"})

    def _job_has_due_work(self, state: dict[str, Any], now: float) -> bool:
        status = state.get("status")
        if status == "attention_required":
            return any(
                _step(state, definition.step_key)["status"]
                in {"pending", "retrying"}
                and float(
                    _step(state, definition.step_key).get("next_attempt_epoch") or 0
                )
                <= now
                for definition in self._destinations(state)
                if definition.publish_on_attention
            )
        if status == "completed_degraded":
            return any(
                _step(state, definition.step_key)["status"] in {"pending", "retrying"}
                and float(
                    _step(state, definition.step_key).get("next_attempt_epoch") or 0
                )
                <= now
                for definition in self._destinations(state)
            )
        if status not in {"queued", "running"}:
            return False

        # Core steps are strictly sequential. A downstream pending step is not
        # actionable while the current step is sleeping for retry; otherwise
        # the worker hot-loops, repeatedly journals ``finalization_running``,
        # and can fill the disk while doing no work.
        for key in CORE_STEPS:
            step = _step(state, key)
            step_status = step["status"]
            if step_status in {"done", "skipped"}:
                continue
            if step_status == "pending":
                return True
            if step_status == "retrying":
                return float(step.get("next_attempt_epoch") or 0) <= now
            return False

        # Destinations are independent, so another destination may be due even
        # while its sibling is delayed or waiting for an operator retry.
        destination_due = False
        destinations_terminal = True
        for definition in self._destinations(state):
            step = _step(state, definition.step_key)
            step_status = step["status"]
            if step_status in {"done", "skipped"}:
                continue
            destinations_terminal = False
            if step_status == "pending" or (
                step_status == "retrying"
                and float(step.get("next_attempt_epoch") or 0) <= now
            ):
                destination_due = True
        if destination_due:
            return True
        if not destinations_terminal:
            return False

        purge = _step(state, "purge_local_sources")
        return purge["status"] == "pending" or (
            purge["status"] == "retrying"
            and float(purge.get("next_attempt_epoch") or 0) <= now
        )

    def _require_state(self, job_id: str) -> dict[str, Any]:
        state = self._jobs.get(str(job_id or "").strip())
        if state is None:
            raise FinalizationNotFoundError("Finalization job was not found.")
        return state


def _initial_steps(
    settings: Mapping[str, Any],
    destinations: tuple[DestinationPluginDefinition, ...],
) -> list[dict[str, Any]]:
    steps = [_ordinary_step("commit_submission", enabled=True)]
    steps.extend(_ordinary_step(key, enabled=True) for key in CORE_STEPS)
    steps.extend(
        _destination_step(
            definition,
            enabled=definition.enabled_for(settings),
        )
        for definition in destinations
    )
    steps.extend(_ordinary_step(key, enabled=True) for key in FINAL_STEPS)
    return steps


def _ordinary_step(key: str, *, enabled: bool) -> dict[str, Any]:
    status = "done" if key == "commit_submission" else "pending" if enabled else "skipped"
    step: dict[str, Any] = {
        "key": key,
        "label": STEP_LABELS[key],
        "enabled": enabled,
        "status": status,
        "attempts": 1 if key == "commit_submission" else 0,
    }
    if not enabled:
        step["details"] = {"reason": "disabled"}
    return step


def _destination_step(
    definition: DestinationPluginDefinition,
    *,
    enabled: bool,
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "key": definition.step_key,
        "label": definition.label,
        "plugin_key": definition.plugin_key,
        "destination": definition.destination,
        "policy": definition.policy(),
        "enabled": enabled,
        "status": "pending" if enabled else "skipped",
        "attempts": 0,
    }
    if not enabled:
        step["details"] = {"reason": "disabled"}
    return step


def _step(state: dict[str, Any], key: str) -> dict[str, Any]:
    for step in state.get("steps", []):
        if step.get("key") == key:
            return step
    raise FinalizationError(f"Finalization state is missing step {key}.")


def _coerce_step_result(value: StepResult | Mapping[str, Any] | None) -> StepResult:
    if value is None:
        return StepResult()
    if isinstance(value, StepResult):
        return value
    status = str(value.get("status") or "done")
    details = value.get("details")
    if details is None:
        details = {key: deepcopy(item) for key, item in value.items() if key != "status"}
    return StepResult(status=status, details=dict(details or {}))


def _submission_started_at(payload: dict[str, Any], now: float) -> dt.datetime:
    for key in ("timestamp_start", "started_at"):
        try:
            return _parse_datetime(payload.get(key))
        except (TypeError, ValueError):
            continue
    return dt.datetime.fromtimestamp(now, tz=dt.timezone.utc)


def _parse_datetime(value: Any) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _job_id(study_id: str, participant_id: str, submission_id: str) -> str:
    digest = hashlib.sha256(f"{study_id}\0{participant_id}\0{submission_id}".encode("utf-8")).hexdigest()[:24]
    return f"finalization-{digest}"


def _submission_key(study_id: str, participant_id: str, submission_id: str) -> str:
    return f"{study_id}\0{participant_id}\0{submission_id}"


def _destination_job_id(
    finalization_job_id: str,
    destination: str,
    publication_generation: int = 1,
) -> str:
    return f"{finalization_job_id}-g{max(1, int(publication_generation))}-{destination}"


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _iso_time(epoch: float) -> str:
    return dt.datetime.fromtimestamp(float(epoch), tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
