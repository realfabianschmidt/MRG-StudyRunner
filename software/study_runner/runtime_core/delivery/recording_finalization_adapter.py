"""Finalization state-machine adapter for recording artifacts."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from dataclasses import asdict

from study_runner.data_core.host.recording_quality import (
    producer_stop_failures,
    split_validation_issues,
    validation_details,
    validation_error,
)

from .finalization_service import (
    FinalizationContext,
    FinalizationError,
    QualityAttentionError,
    StepResult,
)


class RuntimeRecordingFinalizationAdapter:
    """Persistent finalization bridge for recording, validation, and merge."""

    def __init__(
        self,
        runtime: Any,
        *,
        write_end_marker: Callable[[FinalizationContext], Mapping[str, Any] | None] | None = None,
        end_session_producers: Callable[[FinalizationContext], Mapping[str, Any] | None] | None = None,
        tail_timeout_seconds: float | None = None,
    ) -> None:
        self.runtime = runtime
        self.write_end_marker = write_end_marker
        self.end_session_producers = end_session_producers
        self.tail_timeout_seconds = tail_timeout_seconds

    def freeze(self, context: FinalizationContext) -> StepResult:
        """End marker, wait for every stream to pass it, freeze, tell the sensors.

        Sensors are no longer stopped here. They keep streaming for the next
        participant; the recording waits until each regular stream has data
        after the end marker, so the file covers the whole session window.
        The session-end notice only resets per-participant setup state and
        cannot affect the recorded data, so its failures are reported but are
        not quality failures.
        """

        if not context.recording_expected:
            details: dict[str, Any] = {"reason": "no_recording_source_selected"}
            if self.end_session_producers is not None:
                details["producers"] = self._notify_session_end(context)
            return StepResult("skipped", details)
        details: dict[str, Any] = {}
        callback_failures: list[str] = []
        end_marker: dict[str, Any] = {}
        if self.write_end_marker is not None:
            try:
                end_marker = dict(self.write_end_marker(context) or {})
                details["end_marker"] = end_marker
            except Exception as error:
                callback_failures.append(f"end marker: {type(error).__name__}: {error}")
        wait_for_tail = getattr(self.runtime, "wait_for_stream_tail", None)
        if callable(wait_for_tail):
            details["stream_tail"] = dict(
                wait_for_tail(
                    context.paths,
                    _marker_lsl_timestamp(end_marker),
                    timeout_seconds=self.tail_timeout_seconds,
                )
                or {}
            )
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
        if self.end_session_producers is not None:
            details["producers"] = self._notify_session_end(context)
        if callback_failures:
            raise FinalizationError(
                "recording freeze completed with quality failures: "
                + "; ".join(callback_failures)
            )
        return StepResult("done", details)

    def _notify_session_end(self, context: FinalizationContext) -> dict[str, Any]:
        try:
            details = dict(self.end_session_producers(context) or {})  # type: ignore[misc]
        except Exception as error:
            details = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        warnings = context.state.setdefault("warnings", [])
        for failure in producer_stop_failures(details):
            message = f"session_end_notice: {failure}"
            if message not in warnings:
                warnings.append(message)
        return details

    def validate_sources(self, context: FinalizationContext) -> StepResult:
        if not context.recording_expected:
            return StepResult("skipped", {"reason": "no_recording_source_selected"})
        inspections, report = self.runtime.inspect_sources(context.paths)
        details = validation_details(report, inspections=inspections)
        if not report.ok:
            blocking, warnings = split_validation_issues(report.issues)
            if warnings and not blocking:
                # Readable, mergeable data with gaps: an operator may accept
                # it with a reason, and processing then continues degraded.
                raise QualityAttentionError(
                    validation_error("source validation", report),
                    issues=[asdict(issue) for issue in warnings],
                    details=details,
                )
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


def _marker_lsl_timestamp(end_marker: Mapping[str, Any]) -> float | None:
    value = end_marker.get("marker_lsl_timestamp")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def step_attempt(context: FinalizationContext, step_key: str) -> int:
    for step in context.state.get("steps") or []:
        if isinstance(step, Mapping) and step.get("key") == step_key:
            return max(1, int(step.get("attempts") or 1))
    return 1
