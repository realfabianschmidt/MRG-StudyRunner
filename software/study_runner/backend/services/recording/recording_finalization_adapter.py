"""Finalization state-machine adapter for recording artifacts."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from ..delivery.finalization_service import FinalizationContext, FinalizationError, StepResult
from .recording_quality import (
    producer_stop_failures,
    validation_details,
    validation_error,
)


class RuntimeRecordingFinalizationAdapter:
    """Persistent finalization bridge for recording, validation, and merge."""

    def __init__(
        self,
        runtime: Any,
        *,
        write_end_marker: Callable[[FinalizationContext], Mapping[str, Any] | None] | None = None,
        stop_producers: Callable[[FinalizationContext], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.write_end_marker = write_end_marker
        self.stop_producers = stop_producers

    def freeze(self, context: FinalizationContext) -> StepResult:
        if not context.recording_expected:
            return StepResult("skipped", {"reason": "no_recording_source_selected"})
        details: dict[str, Any] = {}
        callback_failures: list[str] = []
        if self.write_end_marker is not None:
            try:
                details["end_marker"] = dict(self.write_end_marker(context) or {})
            except Exception as error:
                callback_failures.append(f"end marker: {type(error).__name__}: {error}")
        if self.stop_producers is not None:
            try:
                producer_details = dict(self.stop_producers(context) or {})
                details["producers"] = producer_details
                callback_failures.extend(producer_stop_failures(producer_details))
            except Exception as error:
                callback_failures.append(f"producer stop: {type(error).__name__}: {error}")
        details["worker"] = self.runtime.freeze_worker(
            context.paths,
            command_id=(
                f"freeze-{context.state['job_id']}-"
                f"a{step_attempt(context, 'freeze_recording')}"
            ),
        )
        worker_quality_failures = details["worker"].get("quality_failures")
        if isinstance(worker_quality_failures, list):
            callback_failures.extend(
                f"worker: {failure}"
                for failure in worker_quality_failures
                if str(failure).strip()
            )
        if callback_failures:
            raise FinalizationError(
                "recording freeze completed with quality failures: "
                + "; ".join(callback_failures)
            )
        return StepResult("done", details)

    def validate_sources(self, context: FinalizationContext) -> StepResult:
        if not context.recording_expected:
            return StepResult("skipped", {"reason": "no_recording_source_selected"})
        inspections, report = self.runtime.inspect_sources(context.paths)
        details = validation_details(report, inspections=inspections)
        if not report.ok:
            raise FinalizationError(validation_error("source validation", report))
        return StepResult("done", details)

    def merge(self, context: FinalizationContext) -> StepResult:
        if not context.recording_expected:
            return StepResult("skipped", {"reason": "no_recording_source_selected"})
        details = self.runtime.merge(
            context.paths,
            command_id=(
                f"merge-{context.state['job_id']}-"
                f"a{step_attempt(context, 'merge_xdf')}"
            ),
        )
        return StepResult("done", {**details, "path": "derived/session.xdf"})

    def validate_merge(self, context: FinalizationContext) -> StepResult:
        if not context.recording_expected:
            return StepResult("skipped", {"reason": "no_recording_source_selected"})
        merged, report = self.runtime.inspect_merge(context.paths)
        details = validation_details(report, inspections=[merged])
        if not report.ok:
            raise FinalizationError(validation_error("merge parity", report))
        shutdown = self.runtime.shutdown_worker(context.paths)
        details["worker_shutdown"] = shutdown
        if not shutdown.get("ok", False):
            warning = f"recording_worker_shutdown: {shutdown.get('warning') or 'unknown error'}"
            if warning not in context.state["warnings"]:
                context.state["warnings"].append(warning)
        return StepResult("done", details)


def step_attempt(context: FinalizationContext, step_key: str) -> int:
    for step in context.state.get("steps") or []:
        if isinstance(step, Mapping) and step.get("key") == step_key:
            return max(1, int(step.get("attempts") or 1))
    return 1
