from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import threading
import time
from typing import Any, Callable
import weakref

from study_runner.plugin_framework.plugin_api import IntegrationContext, IntegrationPlugin


DEFAULT_POLL_INTERVAL_MS = 2000
DEFAULT_REQUEST_TIMEOUT_MS = 1000
DEFAULT_MAX_POLL_WORKERS = 4

StatusLoader = Callable[[str, IntegrationContext], dict[str, Any]]


class PluginHealthPoller:
    """Manifest-paced, stale-while-revalidate cache for plugin health.

    A plugin owns at most one queued or running poll.  The fixed executor bounds
    concurrency, and snapshots never wait for a handler to finish.
    """

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        max_workers: int = DEFAULT_MAX_POLL_WORKERS,
    ) -> None:
        if isinstance(max_workers, bool) or int(max_workers) <= 0:
            raise ValueError("max_workers must be a positive integer")
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=int(max_workers),
            thread_name_prefix="sensor-health",
        )
        self._executor_finalizer = weakref.finalize(
            self,
            _shutdown_executor,
            self._executor,
        )

    def snapshot(
        self,
        plugin: IntegrationPlugin,
        context: IntegrationContext,
        manifest: dict[str, Any],
        status_loader: StatusLoader,
        *,
        now_monotonic: float | None = None,
        now_epoch_ms: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now_monotonic = self._monotonic_clock() if now_monotonic is None else now_monotonic
        now_epoch_ms = self._epoch_ms() if now_epoch_ms is None else now_epoch_ms
        poll_interval_ms = _positive_manifest_int(
            manifest.get("poll_interval_ms"),
            DEFAULT_POLL_INTERVAL_MS,
        )
        request_timeout_ms = _positive_manifest_int(
            manifest.get("request_timeout_ms"),
            DEFAULT_REQUEST_TIMEOUT_MS,
        )

        self._harvest(plugin.key)
        with self._lock:
            state = self._states.setdefault(
                plugin.key,
                _new_poll_state(_pending_status(plugin, context)),
            )
            future = state.get("future")
            poll_has_future = isinstance(future, Future)
            poll_in_flight = poll_has_future and not future.done()
            current_latency_ms, current_timed_out = self._observe_in_flight(
                state,
                now_monotonic,
                request_timeout_ms,
                poll_in_flight,
            )

            last_started = state.get("last_poll_started_monotonic")
            poll_due = last_started is None or (
                now_monotonic - float(last_started)
            ) * 1000 >= poll_interval_ms
            if poll_due and not poll_has_future and not self._closed:
                self._schedule_locked(
                    state,
                    plugin.key,
                    context,
                    status_loader,
                    request_timeout_ms=request_timeout_ms,
                    now_monotonic=now_monotonic,
                    now_epoch_ms=now_epoch_ms,
                )
                future = state.get("future")
                poll_in_flight = isinstance(future, Future) and not future.done()
                current_latency_ms = 0.0
                current_timed_out = False

            status = _safe_copy(state["status"])
            diagnostics = _diagnostics(
                state,
                manifest,
                poll_interval_ms=poll_interval_ms,
                request_timeout_ms=request_timeout_ms,
                now_monotonic=now_monotonic,
                now_epoch_ms=now_epoch_ms,
                poll_in_flight=poll_in_flight,
                current_latency_ms=current_latency_ms,
                current_timed_out=current_timed_out,
                closed=self._closed,
            )
        return status, diagnostics

    def close(self, *, wait: bool = False) -> None:
        """Cancel queued work and release idle executor threads.

        A Python thread already executing third-party code cannot be killed
        safely.  It keeps its existing bounded worker until the handler returns;
        no additional call for that plugin is ever submitted concurrently.
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            for state in self._states.values():
                future = state.get("future")
                if isinstance(future, Future) and not future.running():
                    future.cancel()
        if self._executor_finalizer.alive:
            self._executor_finalizer.detach()
            _shutdown_executor(self._executor, wait=wait)

    def _observe_in_flight(
        self,
        state: dict[str, Any],
        now_monotonic: float,
        request_timeout_ms: int,
        poll_in_flight: bool,
    ) -> tuple[float, bool]:
        started = state.get("in_flight_started_monotonic")
        latency_ms = (
            max(0.0, now_monotonic - float(started)) * 1000
            if poll_in_flight and started is not None
            else 0.0
        )
        timed_out = poll_in_flight and latency_ms > float(
            state.get("in_flight_timeout_ms") or request_timeout_ms
        )
        if timed_out and not state.get("in_flight_timeout_reported"):
            state["in_flight_timeout_reported"] = True
            state["timeout_count"] = int(state.get("timeout_count") or 0) + 1
        return latency_ms, timed_out

    def _schedule_locked(
        self,
        state: dict[str, Any],
        plugin_key: str,
        context: IntegrationContext,
        status_loader: StatusLoader,
        *,
        request_timeout_ms: int,
        now_monotonic: float,
        now_epoch_ms: int,
    ) -> None:
        state["last_poll_started_monotonic"] = now_monotonic
        state["last_poll_started_epoch_ms"] = now_epoch_ms
        state["in_flight_started_monotonic"] = now_monotonic
        state["in_flight_timeout_ms"] = request_timeout_ms
        state["in_flight_timeout_reported"] = False
        try:
            state["future"] = self._executor.submit(
                _load_status,
                status_loader,
                plugin_key,
                context,
                self._monotonic_clock,
                self._wall_clock,
            )
        except RuntimeError as error:
            state["future"] = None
            state["last_poll_error"] = str(error)

    def _harvest(self, plugin_key: str) -> None:
        with self._lock:
            state = self._states.get(plugin_key)
            future = state.get("future") if state else None
            if not isinstance(future, Future) or not future.done():
                return
            started_monotonic = state.get("in_flight_started_monotonic")
            timeout_ms = int(state.get("in_flight_timeout_ms") or DEFAULT_REQUEST_TIMEOUT_MS)

        try:
            result = future.result()
        except Exception as error:  # defensive boundary around executor/plugin code
            result = {
                "status": None,
                "completed_monotonic": self._monotonic_clock(),
                "completed_epoch_ms": self._epoch_ms(),
                "error": str(error),
            }

        with self._lock:
            state = self._states.get(plugin_key)
            if state is None or state.get("future") is not future:
                return
            self._apply_result(
                state,
                result,
                started_monotonic=started_monotonic,
                timeout_ms=timeout_ms,
            )

    @staticmethod
    def _apply_result(
        state: dict[str, Any],
        result: dict[str, Any],
        *,
        started_monotonic: float | None,
        timeout_ms: int,
    ) -> None:
        completed_monotonic = float(result["completed_monotonic"])
        effective_started = (
            completed_monotonic if started_monotonic is None else float(started_monotonic)
        )
        latency_ms = round(max(0.0, completed_monotonic - effective_started) * 1000, 3)
        timed_out = latency_ms > timeout_ms
        if timed_out and not state.get("in_flight_timeout_reported"):
            state["timeout_count"] = int(state.get("timeout_count") or 0) + 1

        status = result.get("status")
        error = result.get("error")
        if isinstance(status, dict):
            state["status"] = _safe_copy(status)
            state["has_completed_status"] = True
        elif not state.get("has_completed_status"):
            state["status"] = {
                **state["status"],
                "status": "failed",
                "last_message": f"Health poll failed: {error or 'unknown error'}",
            }

        state.update(
            {
                "future": None,
                "in_flight_started_monotonic": None,
                "in_flight_timeout_ms": None,
                "in_flight_timeout_reported": False,
                "last_poll_completed_monotonic": completed_monotonic,
                "last_poll_completed_epoch_ms": int(result["completed_epoch_ms"]),
                "last_poll_latency_ms": latency_ms,
                "last_poll_timed_out": timed_out,
                "last_poll_ok": not bool(error),
                "last_poll_error": str(error) if error else None,
                "max_poll_latency_ms": round(
                    max(float(state.get("max_poll_latency_ms") or 0.0), latency_ms),
                    3,
                ),
                "poll_count": int(state.get("poll_count") or 0) + 1,
            }
        )

    def _epoch_ms(self) -> int:
        return int(round(self._wall_clock() * 1000))


def _load_status(
    status_loader: StatusLoader,
    plugin_key: str,
    context: IntegrationContext,
    monotonic_clock: Callable[[], float],
    wall_clock: Callable[[], float],
) -> dict[str, Any]:
    try:
        status = status_loader(plugin_key, context)
        error = None
    except Exception as caught_error:
        status = None
        error = str(caught_error)
    return {
        "status": status,
        "completed_monotonic": monotonic_clock(),
        "completed_epoch_ms": int(round(wall_clock() * 1000)),
        "error": error,
    }


def _new_poll_state(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "has_completed_status": False,
        "future": None,
        "poll_count": 0,
        "timeout_count": 0,
        "last_poll_started_monotonic": None,
        "last_poll_started_epoch_ms": None,
        "last_poll_completed_monotonic": None,
        "last_poll_completed_epoch_ms": None,
        "last_poll_latency_ms": None,
        "last_poll_timed_out": False,
        "last_poll_ok": None,
        "last_poll_error": None,
        "max_poll_latency_ms": 0.0,
        "in_flight_started_monotonic": None,
        "in_flight_timeout_ms": None,
        "in_flight_timeout_reported": False,
    }


def _pending_status(plugin: IntegrationPlugin, context: IntegrationContext) -> dict[str, Any]:
    section = context.hardware_config.get(plugin.config_key)
    section = section if isinstance(section, dict) else {}
    configured_enabled = bool(section.get("enabled", False))
    return {
        "key": plugin.key,
        "label": plugin.label,
        "category": plugin.category,
        "config_key": plugin.config_key,
        "configured_enabled": configured_enabled,
        "runtime_enabled": configured_enabled,
        "enabled": configured_enabled,
        "status": "pending" if configured_enabled else "disabled",
        "last_message": "Health status poll is pending.",
        "last_activity_at": None,
        "device_label": plugin.label,
        "can_start": plugin.can_start,
        "can_stop": plugin.can_stop,
        "can_restart": plugin.can_restart,
        "can_toggle": plugin.can_toggle,
        "has_lsl": plugin.has_lsl,
        "has_recording": plugin.has_recording,
    }


def _diagnostics(
    state: dict[str, Any],
    manifest: dict[str, Any],
    *,
    poll_interval_ms: int,
    request_timeout_ms: int,
    now_monotonic: float,
    now_epoch_ms: int,
    poll_in_flight: bool,
    current_latency_ms: float,
    current_timed_out: bool,
    closed: bool,
) -> dict[str, Any]:
    completed_monotonic = state.get("last_poll_completed_monotonic")
    cache_age_ms = (
        round(max(0.0, now_monotonic - float(completed_monotonic)) * 1000, 3)
        if completed_monotonic is not None
        else None
    )
    last_started = state.get("last_poll_started_monotonic")
    remaining_ms = (
        0.0
        if last_started is None
        else max(
            0.0,
            poll_interval_ms - max(0.0, now_monotonic - float(last_started)) * 1000,
        )
    )
    if not state.get("has_completed_status"):
        cache_state = "pending"
    elif current_timed_out or (cache_age_ms is not None and cache_age_ms > poll_interval_ms):
        cache_state = "stale"
    else:
        cache_state = "fresh"

    return {
        "poll_interval_ms": poll_interval_ms,
        "request_timeout_ms": request_timeout_ms,
        # Compatibility field: this remains the most recent completed poll.
        "last_poll_epoch_ms": state.get("last_poll_completed_epoch_ms"),
        "last_poll_started_epoch_ms": state.get("last_poll_started_epoch_ms"),
        "last_poll_completed_epoch_ms": state.get("last_poll_completed_epoch_ms"),
        "last_poll_latency_ms": state.get("last_poll_latency_ms"),
        "last_poll_timed_out": bool(state.get("last_poll_timed_out")) or current_timed_out,
        "last_poll_ok": state.get("last_poll_ok"),
        "last_poll_error": state.get("last_poll_error"),
        "max_poll_latency_ms": round(float(state.get("max_poll_latency_ms") or 0.0), 3),
        "poll_count": int(state.get("poll_count") or 0),
        "timeout_count": int(state.get("timeout_count") or 0),
        "poll_in_flight": poll_in_flight,
        "current_poll_latency_ms": round(current_latency_ms, 3) if poll_in_flight else None,
        "cache_state": cache_state,
        "cache_age_ms": cache_age_ms,
        "next_poll_due_epoch_ms": int(round(now_epoch_ms + remaining_ms)),
        "poller_closed": closed,
        "clock_domain": manifest.get("clock_domain") or "server",
        "backpressure": dict(manifest.get("backpressure") or {}),
        "capabilities": list(manifest.get("capabilities") or []),
        "stream_count": len(manifest.get("streams") or []),
    }


def _positive_manifest_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_copy(value: dict[str, Any]) -> dict[str, Any]:
    try:
        return deepcopy(value)
    except Exception:
        return dict(value)


def _shutdown_executor(executor: ThreadPoolExecutor, *, wait: bool = False) -> None:
    executor.shutdown(wait=wait, cancel_futures=True)
