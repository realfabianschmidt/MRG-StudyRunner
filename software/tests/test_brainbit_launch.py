"""Guards for how the BrainBit CLI is launched and revived.

The 0.4 releases shipped without the CLI script and without a way to run it from
a packaged build, so BrainBit could not connect at all there. These tests pin
the two mechanisms that fix it: self-dispatch through the frozen executable, and
a watchdog that reacts to a CLI which exits immediately.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.brainbit import adapter, brainbit_realtime_cli
from study_runner.plugins.brainbit import plugin as brainbit_plugin
from study_runner.plugin_framework.plugin_api import IntegrationContext


class FakeRunningProcess:
    pid = 4242

    def poll(self):
        return None


class BuildCliCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_config = adapter._config
        self._original_frozen = getattr(sys, "frozen", None)

    def tearDown(self) -> None:
        adapter._config = self._original_config
        if self._original_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = self._original_frozen

    def test_source_checkout_runs_the_script_with_an_interpreter(self) -> None:
        adapter._config = {
            "python_executable": "/usr/bin/python3",
            "script_path": "/repo/brainbit_realtime_cli.py",
            "scan_seconds": 7,
            "serial_number": "12345",
        }

        command = adapter._build_cli_command()

        self.assertEqual(command[:2], ["/usr/bin/python3", "/repo/brainbit_realtime_cli.py"])
        self.assertIn("--scan-seconds", command)
        self.assertEqual(command[command.index("--scan-seconds") + 1], "7")
        self.assertEqual(command[command.index("--serial-number") + 1], "12345")
        self.assertNotIn("--brainbit-cli", command)

    def test_packaged_build_reinvokes_its_own_executable(self) -> None:
        sys.frozen = True
        adapter._config = {"python_executable": "", "script_path": "/bundle/does-not-exist.py"}

        command = adapter._build_cli_command()

        self.assertEqual(command[:2], [sys.executable, "--brainbit-cli"])
        self.assertNotIn("/bundle/does-not-exist.py", command)

    def test_no_interpreter_and_not_packaged_cannot_launch(self) -> None:
        if hasattr(sys, "frozen"):
            del sys.frozen
        adapter._config = {"python_executable": "", "script_path": "/repo/cli.py"}

        self.assertIsNone(adapter._build_cli_command())

    def test_optional_device_targets_are_omitted_when_empty(self) -> None:
        adapter._config = {
            "python_executable": "python",
            "script_path": "cli.py",
            "serial_number": "",
            "device_address": "",
            "device_name": "",
        }

        command = adapter._build_cli_command()

        self.assertNotIn("--serial-number", command)
        self.assertNotIn("--device-address", command)
        self.assertNotIn("--device-name", command)


class ExitReasonTests(unittest.TestCase):
    def test_clean_exit_has_no_reason(self) -> None:
        self.assertIsNone(adapter._exit_reason(0))
        self.assertIsNone(adapter._exit_reason(None))

    def test_known_codes_map_to_operator_messages(self) -> None:
        cases = {
            brainbit_realtime_cli.EXIT_NO_DEVICE_FOUND: ("brainbit.error.deviceNotFound", True),
            brainbit_realtime_cli.EXIT_BLE_UNAVAILABLE: ("brainbit.error.bluetoothUnavailable", False),
            brainbit_realtime_cli.EXIT_MISSING_DEPENDENCY: ("brainbit.error.missingDependency", False),
        }
        for exit_code, (detail_key, retry) in cases.items():
            with self.subTest(exit_code=exit_code):
                reason = adapter._exit_reason(exit_code)
                self.assertEqual(reason["detail_key"], detail_key)
                self.assertEqual(reason["retry"], retry)
                self.assertTrue(reason["message"])

    def test_unknown_nonzero_code_is_treated_as_a_crash(self) -> None:
        reason = adapter._exit_reason(1)

        self.assertEqual(reason["detail_key"], "brainbit.error.crashed")
        self.assertTrue(reason["retry"])


class RestartAfterExitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_start = adapter.start
        self.starts: list[bool] = []
        adapter.start = lambda: self.starts.append(True)
        adapter._config = {"auto_restart": True, "python_executable": "python", "script_path": "cli.py"}
        adapter._latest_state = {}
        adapter._process = None
        adapter._desired_running = True
        adapter._auto_restart_count = 0
        adapter._last_auto_restart_at = 0.0
        adapter._last_exit_at = 0.0
        adapter._last_exit_code = None

    def tearDown(self) -> None:
        adapter.start = self._original_start
        adapter._desired_running = False
        adapter._latest_state = {}
        adapter._config = {}

    def test_retryable_exit_restarts_and_keeps_watching(self) -> None:
        adapter._last_exit_code = brainbit_realtime_cli.EXIT_NO_DEVICE_FOUND
        adapter._last_exit_at = 100.0

        keep_watching = adapter._maybe_restart_after_exit(now_value=200.0)

        self.assertTrue(keep_watching)
        self.assertEqual(self.starts, [True])
        self.assertEqual(adapter._auto_restart_count, 1)
        self.assertEqual(adapter._latest_state.get("status"), "restarting")

    def test_backoff_delays_the_next_attempt(self) -> None:
        adapter._last_exit_code = brainbit_realtime_cli.EXIT_NO_DEVICE_FOUND
        adapter._last_exit_at = 100.0

        keep_watching = adapter._maybe_restart_after_exit(now_value=101.0)

        self.assertTrue(keep_watching)
        self.assertEqual(self.starts, [])

    def test_bluetooth_off_is_not_retried(self) -> None:
        adapter._last_exit_code = brainbit_realtime_cli.EXIT_BLE_UNAVAILABLE
        adapter._last_exit_at = 100.0

        keep_watching = adapter._maybe_restart_after_exit(now_value=500.0)

        self.assertFalse(keep_watching)
        self.assertEqual(self.starts, [])

    def test_clean_exit_stops_the_watchdog(self) -> None:
        adapter._last_exit_code = 0

        self.assertFalse(adapter._maybe_restart_after_exit(now_value=500.0))
        self.assertEqual(self.starts, [])

    def test_exhausted_attempts_report_a_final_state(self) -> None:
        adapter._last_exit_code = brainbit_realtime_cli.EXIT_NO_DEVICE_FOUND
        adapter._last_exit_at = 100.0
        adapter._auto_restart_count = 3

        keep_watching = adapter._maybe_restart_after_exit(now_value=900.0)

        self.assertFalse(keep_watching)
        self.assertEqual(self.starts, [])
        self.assertEqual(adapter._latest_state.get("status"), "failed")
        self.assertEqual(adapter._latest_state.get("status_detail_key"), "brainbit.error.deviceNotFound")
        self.assertEqual(adapter._latest_state.get("status_detail_hint_key"), "brainbit.error.retriesExhausted")

    def test_intentional_stop_ends_the_watchdog(self) -> None:
        adapter._desired_running = False
        adapter._last_exit_code = brainbit_realtime_cli.EXIT_NO_DEVICE_FOUND

        self.assertFalse(adapter._check_connection_health_once(now=500.0))
        self.assertEqual(self.starts, [])

    def test_running_process_is_left_alone(self) -> None:
        adapter._process = FakeRunningProcess()
        adapter._last_any_line_at = 499.0
        adapter._latest_state = {"status": "connected"}

        self.assertTrue(adapter._check_connection_health_once(now=500.0))
        self.assertEqual(self.starts, [])


class BackoffTests(unittest.TestCase):
    def test_backoff_grows_and_is_capped(self) -> None:
        self.assertEqual(adapter._restart_backoff_seconds(0), 5.0)
        self.assertEqual(adapter._restart_backoff_seconds(1), 15.0)
        self.assertEqual(adapter._restart_backoff_seconds(2), 45.0)
        self.assertEqual(adapter._restart_backoff_seconds(10), 300.0)


class CliDeviceSelectionTests(unittest.TestCase):
    """A configured headset that is absent must not block the session."""

    class Args:
        def __init__(self, **kwargs) -> None:
            self.serial_number = kwargs.get("serial_number", "")
            self.device_address = kwargs.get("device_address", "")
            self.device_name = kwargs.get("device_name", "")
            self.device_index = kwargs.get("device_index", 0)

    class SensorInfo:
        def __init__(self, serial: str, address: str, name: str) -> None:
            self.SerialNumber = serial
            self.Address = address
            self.Name = name

    def test_matching_serial_is_selected(self) -> None:
        sensors = [
            self.SensorInfo("111", "AA:BB", "BrainBit"),
            self.SensorInfo("222", "CC:DD", "BrainBit"),
        ]

        index, info, source = brainbit_realtime_cli._select_sensor_info(
            sensors, self.Args(serial_number="222")
        )

        self.assertEqual(index, 1)
        self.assertIs(info, sensors[1])
        self.assertEqual(source, "serial_number")

    def test_missing_serial_reports_not_found_without_raising(self) -> None:
        sensors = [self.SensorInfo("111", "AA:BB", "BrainBit")]

        index, info, source = brainbit_realtime_cli._select_sensor_info(
            sensors, self.Args(serial_number="999")
        )

        self.assertIsNone(index)
        self.assertIsNone(info)
        self.assertIn("not found", source)


class RuntimeDirTests(unittest.TestCase):
    """Packaged builds must not write logs into the read-only app bundle."""

    def setUp(self) -> None:
        self._original_frozen = getattr(sys, "frozen", None)
        self.context = IntegrationContext(
            base_dir=Path("/bundle/_internal"),
            data_dir=Path("/writable/saved_results"),
            hardware_config={},
            local_secrets={},
            local_secrets_file=Path("/writable/settings/local_secrets.json"),
        )

    def tearDown(self) -> None:
        if self._original_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = self._original_frozen

    def test_source_checkout_uses_the_in_repo_default(self) -> None:
        if hasattr(sys, "frozen"):
            del sys.frozen

        result = brainbit_plugin._runtime_dir(self.context, None, "plugins/brainbit/logs", "logs")

        self.assertIn("plugins", result.replace("\\", "/"))

    def test_packaged_build_without_setting_uses_the_writable_folder(self) -> None:
        sys.frozen = True

        result = brainbit_plugin._runtime_dir(self.context, None, "plugins/brainbit/logs", "logs")

        self.assertEqual(Path(result), Path("/writable/brainbit/logs"))

    def test_packaged_build_redirects_paths_that_point_into_the_bundle(self) -> None:
        # Settings files written by 0.4 pin the in-repo path explicitly; trusting
        # them in a packaged build would try to write inside the app bundle.
        sys.frozen = True

        result = brainbit_plugin._runtime_dir(
            self.context, "study_runner/plugins/brainbit/logs", "plugins/brainbit/logs", "logs"
        )

        self.assertEqual(Path(result), Path("/writable/brainbit/logs"))

    def test_packaged_build_keeps_an_explicit_external_folder(self) -> None:
        sys.frozen = True
        external = str(Path("/operator/chosen/logs"))

        result = brainbit_plugin._runtime_dir(self.context, external, "plugins/brainbit/logs", "logs")

        self.assertEqual(Path(result), Path(external).resolve())


class CliRuntimePipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_frozen = getattr(sys, "frozen", None)
        self._original_env = {
            key: brainbit_realtime_cli.os.environ.get(key)
            for key in ("STUDY_RUNNER_DISABLE_RUNTIME_PIP", "STUDY_RUNNER_APP_MODE")
        }
        for key in self._original_env:
            brainbit_realtime_cli.os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._original_env.items():
            if value is None:
                brainbit_realtime_cli.os.environ.pop(key, None)
            else:
                brainbit_realtime_cli.os.environ[key] = value
        if self._original_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = self._original_frozen

    def test_packaged_builds_never_run_pip(self) -> None:
        sys.frozen = True

        self.assertTrue(brainbit_realtime_cli._runtime_pip_install_disabled())

    def test_explicit_env_flag_disables_pip(self) -> None:
        if hasattr(sys, "frozen"):
            del sys.frozen
        brainbit_realtime_cli.os.environ["STUDY_RUNNER_DISABLE_RUNTIME_PIP"] = "1"

        self.assertTrue(brainbit_realtime_cli._runtime_pip_install_disabled())

    def test_source_checkout_may_install(self) -> None:
        if hasattr(sys, "frozen"):
            del sys.frozen

        self.assertFalse(brainbit_realtime_cli._runtime_pip_install_disabled())


if __name__ == "__main__":
    unittest.main()
