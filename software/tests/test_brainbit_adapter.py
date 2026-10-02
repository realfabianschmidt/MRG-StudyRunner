from __future__ import annotations

from pathlib import Path
import sys
import time
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.sensors.brainbit import adapter, brainbit_realtime_cli

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fake_lsl import FakePylsl  # noqa: E402


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
        adapter._auto_reconnect_active = False

    def tearDown(self) -> None:
        adapter._auto_reconnect_active = False
        adapter._streams.close()
        adapter._routing_state["forward_to_lsl"] = False
        adapter._routing_state["forward_to_touchdesigner"] = False
        adapter._process = None

    def test_lsl_mirror_is_continuous_when_outlet_exists(self) -> None:
        lsl = FakePylsl()
        adapter._streams.use_backend(lsl)
        adapter._streams.open("eeg")
        adapter._eeg_lsl_channels = ("O1", "O2", "T3", "T4")
        adapter._routing_state["forward_to_lsl"] = False

        adapter._mirror_line_to_lsl('EEG {"O1": 1, "O2": 2, "T3": 3, "T4": 4}')

        self.assertEqual(lsl.outlet("study_runner.brainbit.eeg").rows, [[1.0, 2.0, 3.0, 4.0]])

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
        adapter._auto_reconnect_active = True

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


class _CommandPipe:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        pass


class CommandProcess(FakeProcess):
    def __init__(self) -> None:
        self.stdin = _CommandPipe()


class GuidedConnectionTests(BrainBitAdapterTests):
    """Measure contact and initialize without reconnecting; one setup per participant."""

    def setUp(self) -> None:
        super().setUp()
        from study_runner.plugins.sensors.brainbit.monitor import BrainBitMonitor

        adapter._monitor = BrainBitMonitor()
        adapter._process = CommandProcess()
        adapter._awaiting_scan = False
        adapter._contact_stale = False
        adapter._calibration_needs_initialize = False

    def tearDown(self) -> None:
        super().tearDown()
        adapter._awaiting_scan = False
        adapter._contact_stale = False
        adapter._calibration_needs_initialize = False

    def _stream_eeg(self) -> None:
        adapter._monitor.observe("CONNECTED", {})
        adapter._update_state_from_line('EEG {"O1": 1, "O2": 2, "T3": 3, "T4": 4}')

    def test_cli_is_started_with_a_command_channel_and_manual_calibration(self) -> None:
        adapter._config.update({"script_path": "brainbit_cli.py", "python_executable": "python"})
        command = adapter._build_cli_command()
        self.assertIn("--control-stdin", command)
        self.assertIn("--manual-calibration", command)

    def test_contact_is_measured_on_the_running_connection(self) -> None:
        self._stream_eeg()
        self.assertTrue(adapter.measure_contact())
        self.assertEqual(adapter._process.stdin.lines, ["MEASURE_CONTACT\n"])
        connection = adapter.get_status()["connection"]
        self.assertEqual(connection["phase"], "connected")
        self.assertEqual(connection["signal"]["state"], "measuring")

    def test_initialize_starts_the_calibration_for_this_participant(self) -> None:
        self._stream_eeg()
        adapter._update_state_from_line('QUALITY {"O1": 0.5, "O2": 0.5, "T3": 0.5, "T4": 0.5}')
        self.assertEqual(adapter.get_status()["connection"]["setup"]["state"], "needed")
        self.assertTrue(adapter.calibrate())
        self.assertEqual(adapter._process.stdin.lines, ["CALIBRATE\n"])
        self.assertEqual(adapter.get_status()["connection"]["setup"]["state"], "running")
        adapter._update_state_from_line('CALIB {"event": "FINISHED"}')
        connection = adapter.get_status()["connection"]
        self.assertEqual(connection["setup"]["state"], "done")
        self.assertEqual(connection["signal"]["state"], "good")

    def test_commands_need_a_streaming_band(self) -> None:
        self.assertFalse(adapter.measure_contact())
        self.assertFalse(adapter.calibrate())
        self.assertEqual(adapter._process.stdin.lines, [])

    def test_session_end_requires_contact_and_calibration_for_the_next_person(self) -> None:
        self._stream_eeg()
        adapter._update_state_from_line('QUALITY {"O1": 0.5, "O2": 0.5, "T3": 0.5, "T4": 0.5}')
        adapter._update_state_from_line('CALIB {"event": "FINISHED"}')
        adapter.session_end()
        connection = adapter.get_status()["connection"]
        self.assertEqual(connection["signal"]["state"], "stale")
        self.assertEqual(connection["setup"]["state"], "needed")
        self.assertIn("RESET_CALIBRATION\n", adapter._process.stdin.lines)

    def test_switched_on_nothing_is_searched_until_the_operator_acts(self) -> None:
        adapter._process = None
        adapter.await_scan()
        status = adapter.get_status()
        self.assertTrue(status["running"])
        self.assertEqual(status["connection"]["phase"], "idle")


class OperatorDecidesTests(GuidedConnectionTests):
    """Without auto-reconnect a failed or lost connection waits for the operator."""

    def setUp(self) -> None:
        super().setUp()
        adapter.forget_connection()
        adapter._process_connected = False
        adapter._desired_running = True

    def tearDown(self) -> None:
        super().tearDown()
        adapter.forget_connection()
        adapter._process_connected = False
        adapter._desired_running = False

    def _exit(self, code: int) -> dict:
        adapter._process = None
        adapter._hand_back_to_operator(code)
        return adapter.get_status()

    def test_a_lost_connection_is_reported_and_the_band_is_offered_again(self) -> None:
        adapter._remember_connected_band({"serial": "X1", "name": "BrainBit"})
        status = self._exit(brainbit_realtime_cli.EXIT_CONNECT_FAILED)
        connection = status["connection"]
        self.assertTrue(status["running"])
        self.assertFalse(adapter._desired_running)
        self.assertEqual(connection["phase"], "failed")
        self.assertEqual(connection["detail"], "connection_lost")
        self.assertIn(
            {"id": "serial:x1", "label": "BrainBit X1", "payload": {"serial_number": "X1", "name": "BrainBit"}, "note": "last_used"},
            connection["candidates"],
        )

    def test_nothing_found_is_simply_ready_to_connect(self) -> None:
        connection = self._exit(brainbit_realtime_cli.EXIT_NO_DEVICE_FOUND)["connection"]
        self.assertEqual(connection["phase"], "idle")
        self.assertEqual(connection["detail"], "not_found")

    def test_a_connection_that_never_took_is_a_failed_connect(self) -> None:
        connection = self._exit(brainbit_realtime_cli.EXIT_CONNECT_FAILED)["connection"]
        self.assertEqual(connection["detail"], "connect_failed")

    def test_the_cli_starts_with_the_switch_and_returns_to_the_connected_band(self) -> None:
        adapter._config.update({"script_path": "brainbit_cli.py", "python_executable": "python"})
        command = adapter._build_cli_command()
        self.assertEqual(command[command.index("--auto-reconnect") + 1], "off")
        adapter._remember_connected_band({"serial": "X1"})
        adapter._auto_reconnect_active = True
        command = adapter._build_cli_command()
        self.assertEqual(command[command.index("--auto-reconnect") + 1], "on")
        self.assertEqual(command[command.index("--serial-number") + 1], "X1")

    def test_the_running_cli_is_told_only_when_the_switch_changes(self) -> None:
        adapter.set_auto_reconnect(True)
        adapter.set_auto_reconnect(True)
        adapter.set_auto_reconnect(False)
        self.assertEqual(adapter._process.stdin.lines, ["AUTO_RECONNECT_ON\n", "AUTO_RECONNECT_OFF\n"])

    def test_turning_on_after_a_loss_reconnects_to_the_band(self) -> None:
        adapter._remember_connected_band({"serial": "X1"})
        self._exit(brainbit_realtime_cli.EXIT_CONNECT_FAILED)
        starts: list[bool] = []
        with mock.patch.object(adapter, "start", lambda: starts.append(True)):
            adapter.set_auto_reconnect(True)
        self.assertEqual(starts, [True])

    def test_an_exit_is_not_revived_without_auto_reconnect(self) -> None:
        adapter._last_exit_code = brainbit_realtime_cli.EXIT_CONNECT_FAILED
        adapter._last_exit_at = 0.0
        starts: list[bool] = []
        with mock.patch.object(adapter, "start", lambda: starts.append(True)):
            self.assertTrue(adapter._maybe_restart_after_exit(now_value=500.0))
        self.assertEqual(starts, [])


if __name__ == "__main__":
    unittest.main()
