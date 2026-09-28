from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from study_runner.contracts.plugin_api import PluginContext
from study_runner.plugin_framework.registry import (
    export_interval_sidecars as registry_export_interval_sidecars,
    get_plugin_manifest,
    get_plugin_status,
    get_sample_metadata_model,
    initialize_plugin,
    iter_plugins,
    plugin_is_running,
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

    def build_status(self, context: PluginContext) -> dict[str, Any]:
        plugins: dict[str, dict[str, Any]] = {}
        now_monotonic = self._monotonic_clock()
        poll_started_epoch_ms = self._epoch_ms()

        for plugin in iter_plugins():
            manifest = get_plugin_manifest(plugin.key)
            # Phase 3.4 (api_version 5): `health` now gates polling instead of
            # being declared with no effect. A plugin with nothing worth
            # polling (e.g. a card extension, which only declares
            # `card_contract` and is never shown on any admin surface -- see
            # its manifest's `ui.visibility`) opts out entirely rather than
            # costing a thread-pool slot every poll interval for a status
            # nothing reads.
            if "health" not in (manifest.get("capabilities") or {}):
                continue
            status, coordinator = self._health_poller.snapshot(
                plugin,
                context,
                manifest,
                get_plugin_status,
                now_monotonic=now_monotonic,
                now_epoch_ms=poll_started_epoch_ms,
            )
            plugins[plugin.key] = {
                **status,
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
            "plugins": plugins,
        }

    def close(self, *, wait: bool = False) -> None:
        self._health_poller.close(wait=wait)

    def __enter__(self) -> SensorCoordinator:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    def ensure_running(
        self,
        selected_sensors: dict[str, bool],
        sensor_keys: Iterable[str],
        context: PluginContext,
    ) -> dict[str, Any]:
        """Participant session start: make sure the selected sensors run.

        A sensor that is already running is left untouched -- no initialize,
        no restart -- so a prepared device (connected, contact measured,
        calibrated) is simply recorded. Only a stopped selected sensor is
        started. Sensors the study does not select are not touched here;
        loading the study decides about them (``apply_selection``).
        """

        active_plugins: list[str] = []
        runtime: dict[str, dict[str, Any]] = {}
        for sensor_key in sensor_keys:
            if not selected_sensors.get(sensor_key):
                continue
            try:
                status = get_plugin_status(sensor_key, context)
                if plugin_is_running(status):
                    result = {
                        "ok": True,
                        "plugin": sensor_key,
                        "action": "none",
                        "already_running": True,
                        "status": status,
                    }
                else:
                    initialize_plugin(sensor_key, context)
                    result = self.run_action(sensor_key, "start", context)
                runtime[sensor_key] = result
                if result.get("ok"):
                    active_plugins.append(sensor_key)
            except Exception as error:
                runtime[sensor_key] = {"ok": False, "error": str(error)}
        return {
            "active_plugins": active_plugins,
            "runtime": runtime,
            "coordinator": self.lifecycle_summary(),
        }

    def apply_selection(
        self,
        selected_sensors: dict[str, bool],
        sensor_keys: Iterable[str],
        context: PluginContext,
    ) -> dict[str, Any]:
        """Study load / app start: run exactly the sensors the study needs.

        Selected sensors are started when they are not running yet; every
        other sensor is stopped when it is running. A study without sensors
        therefore leaves no sensor active.
        """

        keys = list(sensor_keys)
        result = self.ensure_running(selected_sensors, keys, context)
        runtime = result["runtime"]
        for sensor_key in keys:
            if selected_sensors.get(sensor_key):
                continue
            try:
                status = get_plugin_status(sensor_key, context)
                if plugin_is_running(status):
                    runtime[sensor_key] = self.run_action(sensor_key, "stop", context)
                else:
                    runtime[sensor_key] = {
                        "ok": True,
                        "plugin": sensor_key,
                        "action": "none",
                        "already_stopped": True,
                    }
            except Exception as error:
                runtime[sensor_key] = {"ok": False, "error": str(error)}
        result["coordinator"] = self.lifecycle_summary()
        return result

    def stop_plugins(self, plugin_keys: Iterable[str], context: PluginContext) -> dict[str, Any]:
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

    def run_action(self, plugin_key: str, action: str, context: PluginContext) -> dict[str, Any]:
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
        context: PluginContext,
        start_epoch: float,
        end_epoch: float,
    ) -> list[dict[str, Any]]:
        return registry_export_interval_sidecars(context, start_epoch, end_epoch)

    def lifecycle_summary(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._lifecycle_state.items()}

    def _epoch_ms(self) -> int:
        return int(round(self._wall_clock() * 1000))
