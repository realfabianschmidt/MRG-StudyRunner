from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.clock_diagnostics import adapter
from study_runner.plugin_framework.plugin_api import PluginContext
from study_runner.plugin_framework.registry import run_trial_marker


class _Outlet:
    def __init__(self) -> None:
        self.samples: list[tuple[list[float], float]] = []

    def push_sample(self, sample, timestamp) -> None:
        self.samples.append((list(sample), float(timestamp)))


class ClockDiagnosticsTests(unittest.TestCase):
    def tearDown(self) -> None:
        adapter.stop()

    def test_event_contains_wall_lsl_and_client_clock_observations(self) -> None:
        outlet = _Outlet()
        adapter._outlet = outlet
        adapter._local_clock = lambda: 42.5
        adapter._event_sequence = 0

        adapter.emit(
            {
                "server_received_epoch_ms": 1_760_000_000_123.0,
                "clock_offset_ms": 12.5,
                "clock_sync_rtt_ms": 24.0,
                "sequence_number": 9,
                "source_epoch_ms": 1_760_000_000_100.0,
            }
        )

        sample, timestamp = outlet.samples[0]
        self.assertEqual(timestamp, 42.5)
        self.assertEqual(
            sample,
            [1_760_000_000_123.0, 42.5, 12.5, 24.0, 9.0, 1_760_000_000_100.0, 1.0],
        )

    def test_hidden_internal_plugin_receives_events_without_hardware_toggle(self) -> None:
        context = PluginContext(
            base_dir=PROJECT_ROOT,
            data_dir=PROJECT_ROOT / "saved_results",
            hardware_config={},
            local_secrets={},
            local_secrets_file=PROJECT_ROOT / "local_secrets.json",
        )
        with patch(
            "study_runner.plugins.clock_diagnostics.adapter.emit"
        ) as emit:
            run_trial_marker({"event_id": "event-1"}, context)

        emit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
