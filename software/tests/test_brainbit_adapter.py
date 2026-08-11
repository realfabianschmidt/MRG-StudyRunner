from __future__ import annotations

from pathlib import Path
import sys
import time
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.brainbit import adapter


class FakeOutlet:
    def __init__(self) -> None:
        self.samples: list[list[float]] = []

    def push_sample(self, values) -> None:
        self.samples.append(list(values))


class FakeProcess:
    pid = 12345

    def poll(self):
        return None


class BrainBitAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        adapter._config = {
            "lsl_enabled": False,
            "disconnect_timeout_ms": 1000,
            "monitor_refresh_ms": 1000,
        }
        adapter._latest_state = {}
        adapter._history.clear()
        adapter._process = FakeProcess()
        adapter._last_activity_at = 0.0
        adapter._last_any_line_at = 0.0
        adapter._last_sensor_activity_at = 0.0
        adapter._last_eeg_at = 0.0
        adapter._last_quality_at = 0.0
        adapter._last_derived_at = 0.0
        adapter._signal_started_at = 0.0
        adapter._process_started_at = 0.0
        adapter._eeg_lsl_channels = ()
        adapter._lsl_stream_health = {}
        adapter._auto_restart_count = 0
        adapter._last_auto_restart_at = 0.0

    def tearDown(self) -> None:
        adapter._lsl_outlets = {}
        adapter._routing_state["forward_to_lsl"] = False
        adapter._routing_state["forward_to_touchdesigner"] = False
        adapter._process = None

    def test_lsl_mirror_is_continuous_when_outlet_exists(self) -> None:
        outlet = FakeOutlet()
        adapter._lsl_outlets = {"EEG": outlet}
        adapter._eeg_lsl_channels = ("O1", "O2", "T3", "T4")
        adapter._routing_state["forward_to_lsl"] = False

        adapter._mirror_line_to_lsl('EEG {"O1": 1, "O2": 2, "T3": 3, "T4": 4}')

        self.assertEqual(outlet.samples, [[1.0, 2.0, 3.0, 4.0]])

    def test_quality_updates_contact_state_without_stale(self) -> None:
        adapter._update_state_from_line('QUALITY {"O1": 0.0, "O2": 0.18, "T3": 0.4, "T4": 0.3}')

        status = adapter.get_status()

        self.assertEqual(status["status"], "poor_contact")
        self.assertEqual(status["contact_quality_state"], "poor")
        self.assertIsNotNone(status["seconds_since_last_quality"])
        self.assertNotEqual(status["status"], "stale")

    def test_resist_missing_values_are_visible_but_not_raw_activity(self) -> None:
        adapter._update_state_from_line('RESIST {"O1": null, "O2": 1700, "T3": 1500, "T4": 1600}')

        status = adapter.get_status()

        self.assertEqual(status["status"], "poor_contact")
        self.assertIsNone(status["seconds_since_last_activity"])
        self.assertEqual(status["health"]["raw_eeg"], "waiting")
        self.assertIn("resist", status["latest"])

    def test_eeg_without_derived_metrics_is_warming_up(self) -> None:
        adapter._update_state_from_line('EEG {"O1": 1, "O2": 2, "T3": 3, "T4": 4}')

        status = adapter.get_status()

        self.assertEqual(status["status"], "warming_up")
        self.assertEqual(status["health"]["eeg"], "receiving")
        self.assertEqual(status["health"]["derived_metrics"], "waiting")

    def test_derived_metrics_mark_brainbit_connected(self) -> None:
        adapter._update_state_from_line('EEG {"O1": 1, "O2": 2, "T3": 3, "T4": 4}')
        adapter._update_state_from_line('MENTAL {"Inst_Attention": 0.7, "Inst_Relaxation": 0.3, "Rel_Attention": 0.6, "Rel_Relaxation": 0.4}')

        status = adapter.get_status()

        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["health"]["derived_metrics"], "ready")
        self.assertIsNotNone(status["seconds_since_last_derived"])

    def test_no_output_beyond_timeout_marks_stale(self) -> None:
        adapter._process = FakeProcess()
        adapter._last_any_line_at = 100.0
        adapter._latest_state = {"status": "connected"}

        adapter._check_connection_health_once(now=102.0)

        self.assertEqual(adapter.get_status()["status"], "stale")

    def test_persistent_stale_triggers_auto_restart(self) -> None:
        adapter._config.update({"script_path": "brainbit_cli.py", "python_executable": "python"})
        adapter._process = FakeProcess()
        adapter._signal_started_at = 100.0
        adapter._latest_state = {"status": "connected", "signal_started_epoch": 100.0}
        adapter._auto_restart_count = 0
        adapter._last_auto_restart_at = 0.0
        adapter._desired_running = False

        restarts: list[bool] = []
        original_restart = adapter.restart
        adapter.restart = lambda: restarts.append(True)
        try:
            adapter._check_connection_health_once(now=103.0)
        finally:
            adapter.restart = original_restart

        self.assertEqual(restarts, [True])
        self.assertEqual(adapter._auto_restart_count, 1)
        self.assertEqual(adapter._latest_state.get("status"), "restarting")

    def test_auto_restart_respects_attempt_limit(self) -> None:
        adapter._config.update({"script_path": "brainbit_cli.py", "python_executable": "python"})
        adapter._process = FakeProcess()
        adapter._signal_started_at = 100.0
        adapter._latest_state = {"status": "connected", "signal_started_epoch": 100.0}
        adapter._auto_restart_count = 3
        adapter._last_auto_restart_at = 0.0

        restarts: list[bool] = []
        original_restart = adapter.restart
        adapter.restart = lambda: restarts.append(True)
        try:
            adapter._check_connection_health_once(now=103.0)
        finally:
            adapter.restart = original_restart

        self.assertEqual(restarts, [])

    def test_fresh_raw_eeg_resets_restart_counter(self) -> None:
        adapter._process = FakeProcess()
        adapter._auto_restart_count = 2
        adapter._last_auto_restart_at = 100.0
        adapter._signal_started_at = 100.0
        adapter._last_eeg_at = 150.0
        adapter._last_any_line_at = 150.0
        adapter._latest_state = {
            "status": "connected",
            "signal_started_epoch": 100.0,
            "last_eeg_epoch": 150.0,
        }

        adapter._check_connection_health_once(now=150.2)

        self.assertEqual(adapter._auto_restart_count, 0)

    def test_watchdog_uses_observed_exit_code_before_reader_finalizes(self) -> None:
        class ExitedProcess:
            def poll(self):
                return adapter.EXIT_NO_DEVICE_FOUND

        adapter._process = ExitedProcess()
        adapter._desired_running = True
        adapter._last_exit_code = None
        observed: list[int | None] = []

        with mock.patch.object(
            adapter,
            "_maybe_restart_after_exit",
            side_effect=lambda now: observed.append(adapter._last_exit_code) or True,
        ):
            adapter._check_connection_health_once(now=100.0)

        self.assertEqual(observed, [adapter.EXIT_NO_DEVICE_FOUND])

    def test_stale_reader_generation_does_not_route_old_lines(self) -> None:
        class OldProcess:
            stdout = ['EEG {"O1":1,"O2":2,"T3":3,"T4":4}\n']

            def poll(self):
                return 0

        adapter._process_generation = 2
        routed: list[str] = []

        with mock.patch.object(adapter, "_update_state_from_line", side_effect=lambda line: routed.append(line)):
            adapter._read_output(OldProcess(), generation=1)

        self.assertEqual(routed, [])

    def test_device_identity_without_live_activity_is_not_connected(self) -> None:
        adapter._update_state_from_line('DEVICE {"name": "BrainBit", "serial_number": "ABC123"}')

        status = adapter.get_status()

        self.assertEqual(status["selected_device"]["serial_number"], "ABC123")
        self.assertNotEqual(status["health"]["connection"], "connected")
        self.assertNotEqual(status["status"], "connected")

    def test_process_exit_with_old_device_is_not_connected(self) -> None:
        now = time.time()
        adapter._latest_state = {
            "status": "connected",
            "device": {"name": "BrainBit", "serial_number": "ABC123"},
            "last_any_line_epoch": now,
            "last_sensor_activity_epoch": now,
        }
        adapter._process = None

        status = adapter.get_status()

        self.assertEqual(status["health"]["connection"], "stopped")
        self.assertEqual(status["status"], "stopped")

    def test_scan_candidates_are_exposed(self) -> None:
        adapter._update_state_from_line(
            'SCAN {"index": 1, "name": "BrainBit Black", "address": "AA:BB", "serial": "SN-1", "rssi": -60}'
        )

        status = adapter.get_status()

        self.assertEqual(status["scan_candidates"][0]["serial"], "SN-1")
        self.assertEqual(status["scan_candidates"][0]["address"], "AA:BB")


if __name__ == "__main__":
    unittest.main()
