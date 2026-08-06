from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import call, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.sensor_coordinator_service import SensorCoordinator
from study_runner.plugin_framework.plugin_api import PluginContext, Plugin


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self._now = float(now)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += float(seconds)


def _context() -> PluginContext:
    return PluginContext(
        base_dir=PROJECT_ROOT,
        data_dir=PROJECT_ROOT / "saved_results",
        hardware_config={},
        local_secrets={},
        local_secrets_file=PROJECT_ROOT / "local_secrets.json",
    )


def _plugin(key: str) -> Plugin:
    return Plugin(
        key=key,
        label=key.title(),
        category="test",
        config_key=key,
    )


def _manifest(
    key: str,
    *,
    poll_interval_ms: int = 1000,
    request_timeout_ms: int = 100,
) -> dict:
    return {
        "key": key,
        "poll_interval_ms": poll_interval_ms,
        "request_timeout_ms": request_timeout_ms,
        "clock_domain": "server",
        "backpressure": {},
        "capabilities": ["health"],
        "streams": [],
    }


def _wait_for_poll_count(
    coordinator: SensorCoordinator,
    context: PluginContext,
    plugin_key: str,
    expected: int,
    *,
    timeout: float = 1.0,
) -> dict:
    deadline = time.perf_counter() + timeout
    latest: dict = {}
    while time.perf_counter() < deadline:
        latest = coordinator.build_status(context)
        diagnostics = latest["plugins"][plugin_key]["coordinator"]
        if diagnostics["poll_count"] >= expected:
            return latest
        time.sleep(0.005)
    raise AssertionError(
        f"{plugin_key} did not reach poll_count={expected}: "
        f"{latest.get('plugins', {}).get(plugin_key, {})}"
    )


class SensorCoordinatorTests(unittest.TestCase):
    def test_status_snapshot_uses_manifest_and_returns_cached_poll_result(self) -> None:
        coordinator = SensorCoordinator(monotonic_clock=FakeClock(), wall_clock=lambda: 1000.0)
        context = _context()
        try:
            with patch(
                "study_runner.backend.services.sensor_coordinator_service.get_plugin_status",
                side_effect=lambda key, _context: {"status": "ok", "device_label": key},
            ):
                initial = coordinator.build_status(context)
                status = _wait_for_poll_count(coordinator, context, "brainbit", 1)

            self.assertTrue(initial["ok"])
            self.assertIn("source_epoch_ms", initial["sample_metadata_model"])
            brainbit = status["plugins"]["brainbit"]
            self.assertEqual(brainbit["status"], "ok")
            self.assertEqual(brainbit["manifest"]["clock_domain"], "lsl")
            self.assertEqual(brainbit["coordinator"]["poll_interval_ms"], 1000)
            self.assertEqual(brainbit["coordinator"]["cache_state"], "fresh")
            self.assertEqual(brainbit["coordinator"]["poll_count"], 1)
            self.assertIn("brainbit", status["plugins"])
        finally:
            coordinator.close(wait=True)

    def test_each_plugin_is_polled_only_when_its_manifest_interval_is_due(self) -> None:
        clock = FakeClock(10.0)
        coordinator = SensorCoordinator(monotonic_clock=clock, wall_clock=lambda: 2000.0)
        context = _context()
        plugin = _plugin("paced")
        calls = 0
        calls_lock = threading.Lock()

        def get_status(_key: str, _context: PluginContext) -> dict:
            nonlocal calls
            with calls_lock:
                calls += 1
            return {"key": "paced", "status": "ok"}

        try:
            with (
                patch(
                    "study_runner.backend.services.sensor_coordinator_service.iter_plugins",
                    return_value=(plugin,),
                ),
                patch(
                    "study_runner.backend.services.sensor_coordinator_service.get_plugin_manifest",
                    return_value=_manifest("paced", poll_interval_ms=1000),
                ),
                patch(
                    "study_runner.backend.services.sensor_coordinator_service.get_plugin_status",
                    side_effect=get_status,
                ),
            ):
                coordinator.build_status(context)
                _wait_for_poll_count(coordinator, context, "paced", 1)

                coordinator.build_status(context)
                clock.advance(0.999)
                before_due = coordinator.build_status(context)
                with calls_lock:
                    self.assertEqual(calls, 1)
                self.assertFalse(before_due["plugins"]["paced"]["coordinator"]["poll_in_flight"])

                clock.advance(0.001)
                due = coordinator.build_status(context)
                self.assertTrue(due["plugins"]["paced"]["coordinator"]["poll_in_flight"])
                final = _wait_for_poll_count(coordinator, context, "paced", 2)

            self.assertEqual(final["plugins"]["paced"]["coordinator"]["poll_count"], 2)
            with calls_lock:
                self.assertEqual(calls, 2)
        finally:
            coordinator.close(wait=True)

    def test_slow_plugin_times_out_without_blocking_fast_plugin_or_admin_snapshot(self) -> None:
        clock = FakeClock(50.0)
        coordinator = SensorCoordinator(
            monotonic_clock=clock,
            wall_clock=lambda: 3000.0,
            max_poll_workers=2,
        )
        context = _context()
        slow = _plugin("slow")
        fast = _plugin("fast")
        slow_release = threading.Event()
        slow_finished = threading.Event()
        fast_finished = threading.Event()
        call_counts = {"slow": 0, "fast": 0}
        calls_lock = threading.Lock()

        def get_status(key: str, _context: PluginContext) -> dict:
            with calls_lock:
                call_counts[key] += 1
            if key == "slow":
                slow_release.wait(2.0)
                slow_finished.set()
            else:
                fast_finished.set()
            return {"key": key, "status": "ready"}

        try:
            with (
                patch(
                    "study_runner.backend.services.sensor_coordinator_service.iter_plugins",
                    return_value=(slow, fast),
                ),
                patch(
                    "study_runner.backend.services.sensor_coordinator_service.get_plugin_manifest",
                    side_effect=lambda key: _manifest(
                        key,
                        poll_interval_ms=1000,
                        request_timeout_ms=50,
                    ),
                ),
                patch(
                    "study_runner.backend.services.sensor_coordinator_service.get_plugin_status",
                    side_effect=get_status,
                ),
            ):
                started = time.perf_counter()
                initial = coordinator.build_status(context)
                request_duration = time.perf_counter() - started
                self.assertLess(request_duration, 0.2)
                self.assertTrue(initial["plugins"]["slow"]["coordinator"]["poll_in_flight"])
                self.assertTrue(fast_finished.wait(1.0))

                fast_snapshot = _wait_for_poll_count(coordinator, context, "fast", 1)
                self.assertEqual(fast_snapshot["plugins"]["fast"]["status"], "ready")

                clock.advance(0.100)
                timed_out = coordinator.build_status(context)
                slow_diagnostics = timed_out["plugins"]["slow"]["coordinator"]
                self.assertTrue(slow_diagnostics["poll_in_flight"])
                self.assertTrue(slow_diagnostics["last_poll_timed_out"])
                self.assertEqual(slow_diagnostics["timeout_count"], 1)

                # Even after multiple snapshots and a due interval, the same
                # stuck handler is never submitted a second time.
                clock.advance(2.0)
                coordinator.build_status(context)
                coordinator.build_status(context)
                with calls_lock:
                    self.assertEqual(call_counts["slow"], 1)

                slow_release.set()
                self.assertTrue(slow_finished.wait(1.0))
                completed = _wait_for_poll_count(coordinator, context, "slow", 1)
                self.assertEqual(
                    completed["plugins"]["slow"]["coordinator"]["timeout_count"],
                    1,
                )
        finally:
            slow_release.set()
            coordinator.close(wait=True)

    def test_close_prevents_new_polls_and_is_idempotent(self) -> None:
        coordinator = SensorCoordinator(max_poll_workers=1)
        coordinator.close()
        coordinator.close()
        plugin = _plugin("closed")
        with (
            patch(
                "study_runner.backend.services.sensor_coordinator_service.iter_plugins",
                return_value=(plugin,),
            ),
            patch(
                "study_runner.backend.services.sensor_coordinator_service.get_plugin_manifest",
                return_value=_manifest("closed"),
            ),
            patch(
                "study_runner.backend.services.sensor_coordinator_service.get_plugin_status"
            ) as get_status,
        ):
            status = coordinator.build_status(_context())

        get_status.assert_not_called()
        diagnostics = status["plugins"]["closed"]["coordinator"]
        self.assertTrue(diagnostics["poller_closed"])
        self.assertFalse(diagnostics["poll_in_flight"])

    def test_start_selected_and_stop_plugins_route_lifecycle_through_registry(self) -> None:
        context = _context()
        clock = FakeClock(1.0)
        coordinator = SensorCoordinator(monotonic_clock=clock)
        try:
            with (
                patch("study_runner.backend.services.sensor_coordinator_service.initialize_plugin") as initialize,
                patch(
                    "study_runner.backend.services.sensor_coordinator_service.run_runtime_action",
                    side_effect=lambda key, action, _context: {"ok": True, "integration": key, "action": action},
                ) as runtime_action,
            ):
                started = coordinator.start_selected(
                    {"brainbit": True, "mini_radar": False},
                    ["brainbit", "mini_radar"],
                    context,
                )
                stopped = coordinator.stop_plugins(["brainbit"], context)

            initialize.assert_called_once_with("brainbit", context)
            runtime_action.assert_has_calls(
                [
                    call("brainbit", "start", context),
                    call("mini_radar", "stop", context),
                    call("brainbit", "stop", context),
                ]
            )
            self.assertEqual(started["active_plugins"], ["brainbit"])
            self.assertEqual(stopped["stopped_plugins"], ["brainbit"])
            self.assertEqual(stopped["coordinator"]["brainbit"]["last_action"], "stop")
        finally:
            coordinator.close(wait=True)


if __name__ == "__main__":
    unittest.main()
