from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import call, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.sensor_coordinator_service import SensorCoordinator
from study_runner.integrations.plugin_api import IntegrationContext


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


def _context(temp_dir: str) -> IntegrationContext:
    root = Path(temp_dir)
    return IntegrationContext(
        base_dir=root,
        data_dir=root / "saved_results",
        hardware_config={},
        local_secrets={},
        local_secrets_file=root / "local_secrets.json",
    )


class SensorCoordinatorTests(unittest.TestCase):
    def test_status_snapshot_enriches_integrations_with_manifest_and_poll_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = SensorCoordinator(
                monotonic_clock=FakeClock([1.0, 1.05, 2.0, 2.01, 3.0, 3.01, 4.0, 4.01, 5.0, 5.01, 6.0, 6.01, 7.0, 7.01, 8.0, 8.01]),
                wall_clock=lambda: 1000.0,
            )
            with patch(
                "study_runner.backend.services.sensor_coordinator_service.get_plugin_status",
                side_effect=lambda key, _context: {"status": "ok", "device_label": key},
            ):
                status = coordinator.build_status(_context(temp_dir))

        self.assertTrue(status["ok"])
        self.assertIn("source_epoch_ms", status["sample_metadata_model"])
        brainbit = status["integrations"]["brainbit"]
        self.assertEqual(brainbit["manifest"]["clock_domain"], "lsl")
        self.assertEqual(brainbit["coordinator"]["poll_interval_ms"], 1000)
        self.assertGreater(brainbit["coordinator"]["last_poll_latency_ms"], 0)
        self.assertIn("brainbit", status["plugins"])

    def test_start_selected_and_stop_plugins_route_lifecycle_through_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            context = _context(temp_dir)
            coordinator = SensorCoordinator(monotonic_clock=FakeClock([1.0, 1.01, 2.0, 2.01, 3.0, 3.01]))
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


if __name__ == "__main__":
    unittest.main()
