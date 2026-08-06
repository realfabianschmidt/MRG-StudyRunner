from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from study_runner.plugin_framework.plugin_api import IntegrationContext
from study_runner.plugin_framework.registry import (
    export_interval_sidecars as registry_export_interval_sidecars,
    get_plugin_manifest,
    get_plugin_status,
    get_sample_metadata_model,
    initialize_plugin,
    iter_plugins,
    run_runtime_action,
)

from .plugin_health_poll_service import (
    DEFAULT_MAX_POLL_WORKERS,
    PluginHealthPoller,
)


class SensorCoordinator:
    """Central compatibility layer for plugin lifecycle and diagnostics.

    Status reads use a manifest-paced stale-while-revalidate cache, so plugin
    handlers never execute on the Admin HTTP request thread.
    """

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        max_poll_workers: int = DEFAULT_MAX_POLL_WORKERS,
    ) -> None:
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._lifecycle_state: dict[str, dict[str, Any]] = {}
        self._health_poller = PluginHealthPoller(
            monotonic_clock=monotonic_clock,
            wall_clock=wall_clock,
            max_workers=max_poll_workers,
        )

    def build_status(self, context: IntegrationContext) -> dict[str, Any]:
        integrations: dict[str, dict[str, Any]] = {}
        plugins: dict[str, dict[str, Any]] = {}
        now_monotonic = self._monotonic_clock()
        poll_started_epoch_ms = self._epoch_ms()

        for plugin in iter_plugins():
            manifest = get_plugin_manifest(plugin.key)
            status, coordinator = self._health_poller.snapshot(
                plugin,
                context,
                manifest,
                get_plugin_status,
                now_monotonic=now_monotonic,
                now_epoch_ms=poll_started_epoch_ms,
            )
            integrations[plugin.key] = {
                **status,
                "manifest": manifest,
                "coordinator": coordinator,
            }
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

    def close(self, *, wait: bool = False) -> None:
        self._health_poller.close(wait=wait)

    def __enter__(self) -> SensorCoordinator:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

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

    def _epoch_ms(self) -> int:
        return int(round(self._wall_clock() * 1000))
