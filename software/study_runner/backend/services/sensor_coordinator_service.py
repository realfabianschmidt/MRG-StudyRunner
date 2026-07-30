from __future__ import annotations

import threading
import time
from typing import Any, Iterable, Callable

from study_runner.integrations.plugin_api import IntegrationContext
from study_runner.integrations.registry import (
    export_interval_sidecars as registry_export_interval_sidecars,
    get_plugin_manifest,
    get_plugin_status,
    get_sample_metadata_model,
    initialize_plugin,
    iter_plugins,
    run_runtime_action,
)


class SensorCoordinator:
    """Central compatibility layer for plugin lifecycle and status diagnostics."""

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._poll_state: dict[str, dict[str, Any]] = {}
        self._lifecycle_state: dict[str, dict[str, Any]] = {}

    def build_status(self, context: IntegrationContext) -> dict[str, Any]:
        integrations: dict[str, dict[str, Any]] = {}
        plugins: dict[str, dict[str, Any]] = {}
        poll_started_epoch_ms = self._epoch_ms()

        for plugin in iter_plugins():
            manifest = get_plugin_manifest(plugin.key)
            status, coordinator = self._poll_plugin(plugin.key, context, manifest)
            enriched_status = {
                **status,
                "manifest": manifest,
                "coordinator": coordinator,
            }
            integrations[plugin.key] = enriched_status
            plugins[plugin.key] = {
                "manifest": manifest,
                "coordinator": coordinator,
            }

        return {
            "ok": True,
            "version": 1,
            "poll_started_epoch_ms": poll_started_epoch_ms,
            "timestamp_strategy": {
                "primary": "LSL/XDF for biosignal streams",
                "coordinator": "status, lifecycle, diagnostics, and non-LSL timing metadata",
                "note": "Coordinator RTT/offset diagnostics do not replace source timestamps or LSL clock correction.",
            },
            "sample_metadata_model": get_sample_metadata_model(),
            "integrations": integrations,
            "plugins": plugins,
        }

    def start_selected(
        self,
        selected_sensors: dict[str, bool],
        sensor_keys: Iterable[str],
        context: IntegrationContext,
    ) -> dict[str, Any]:
        active_plugins: list[str] = []
        runtime: dict[str, dict[str, Any]] = {}

        for sensor_key in sensor_keys:
            if selected_sensors.get(sensor_key):
                try:
                    initialize_plugin(sensor_key, context)
                    result = self.run_action(sensor_key, "start", context)
                    runtime[sensor_key] = result
                    if result.get("ok"):
                        active_plugins.append(sensor_key)
                except Exception as error:
                    runtime[sensor_key] = {"ok": False, "error": str(error)}
                continue

            try:
                runtime[sensor_key] = self.run_action(sensor_key, "stop", context)
            except Exception as error:
                runtime[sensor_key] = {"ok": False, "error": str(error)}

        return {
            "active_plugins": active_plugins,
            "runtime": runtime,
            "coordinator": self.lifecycle_summary(),
        }

    def stop_plugins(self, plugin_keys: Iterable[str], context: IntegrationContext) -> dict[str, Any]:
        stopped_plugins = list(plugin_keys)
        runtime: dict[str, dict[str, Any]] = {}
        for plugin_key in stopped_plugins:
            try:
                runtime[plugin_key] = self.run_action(plugin_key, "stop", context)
            except Exception as error:
                runtime[plugin_key] = {"ok": False, "error": str(error)}
        return {
            "stopped_plugins": stopped_plugins,
            "runtime": runtime,
            "coordinator": self.lifecycle_summary(),
        }

    def run_action(self, plugin_key: str, action: str, context: IntegrationContext) -> dict[str, Any]:
        started = self._monotonic_clock()
        result = run_runtime_action(plugin_key, action, context)
        latency_ms = round(max(0.0, self._monotonic_clock() - started) * 1000, 3)
        with self._lock:
            self._lifecycle_state[plugin_key] = {
                "last_action": action,
                "last_action_epoch_ms": self._epoch_ms(),
                "last_action_latency_ms": latency_ms,
                "last_action_ok": bool(result.get("ok")),
            }
        return result

    def export_interval_sidecars(
        self,
        context: IntegrationContext,
        start_epoch: float,
        end_epoch: float,
    ) -> list[dict[str, Any]]:
        return registry_export_interval_sidecars(context, start_epoch, end_epoch)

    def lifecycle_summary(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._lifecycle_state.items()}

    def _poll_plugin(
        self,
        plugin_key: str,
        context: IntegrationContext,
        manifest: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        started = self._monotonic_clock()
        status = get_plugin_status(plugin_key, context)
        latency_ms = round(max(0.0, self._monotonic_clock() - started) * 1000, 3)
        timeout_ms = int(manifest.get("request_timeout_ms") or 1000)
        poll_state = {
            "poll_interval_ms": int(manifest.get("poll_interval_ms") or 2000),
            "request_timeout_ms": timeout_ms,
            "last_poll_epoch_ms": self._epoch_ms(),
            "last_poll_latency_ms": latency_ms,
            "last_poll_timed_out": latency_ms > timeout_ms,
            "clock_domain": manifest.get("clock_domain") or "server",
            "backpressure": dict(manifest.get("backpressure") or {}),
            "capabilities": list(manifest.get("capabilities") or []),
            "stream_count": len(manifest.get("streams") or []),
        }
        with self._lock:
            previous = self._poll_state.get(plugin_key, {})
            max_latency = max(float(previous.get("max_poll_latency_ms") or 0.0), latency_ms)
            poll_count = int(previous.get("poll_count") or 0) + 1
            poll_state["max_poll_latency_ms"] = round(max_latency, 3)
            poll_state["poll_count"] = poll_count
            self._poll_state[plugin_key] = dict(poll_state)
        return status, poll_state

    def _epoch_ms(self) -> int:
        return int(round(self._wall_clock() * 1000))
