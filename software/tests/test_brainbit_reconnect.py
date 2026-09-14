"""Evidence for the BrainBit discovery and reconnection behaviour.

The band cannot be plugged into a test runner, so these tests stand in for it
with a scripted fake scanner. Each one pins a behaviour that a real operator
previously had to work around by hand -- repeatedly pressing Stop and Start
until a connection happened to take.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.sensors.brainbit import brainbit_realtime_cli as cli


class FakeInfo:
    """The handful of attributes the CLI reads off a discovered band."""

    def __init__(self, name="BrainBit", address="AA:BB:CC:DD:EE:FF", serial="") -> None:
        self.Name = name
        self.SensFamily = 1
        self.Address = address
        self.SerialNumber = serial
        self.PairingRequired = False
        self.RSSI = -50


class FakeFamilies:
    LEBrainBit = 1
    LEBrainBitBlack = 2


class FakeSensor:
    """A band that connects and then refuses to be configured.

    That is deliberately the shortest route out of a session: it proves the
    connection was made -- which is all these tests are about -- and returns a
    retryable failure, without having to simulate EEG streaming.
    """

    sens_family = "LEBrainBit2"

    def __init__(self, info) -> None:
        self.info = info
        self.disconnected = 0

    def disconnect(self) -> None:
        self.disconnected += 1


def _make_scanner(*, appears_after_polls=0, bands=(), connect_failures=0):
    """Build a Scanner class that reveals `bands` after a number of polls."""
    # Shared across instances: each retry builds a fresh scanner, and a failure
    # budget that reset with it could never be used up.
    remaining_failures = [connect_failures]
    connected_sensors: list[FakeSensor] = []

    class FakeScanner:
        instances: list["FakeScanner"] = []
        connected = connected_sensors

        def __init__(self, families) -> None:
            self.sensorsChanged = None
            self.polls = 0
            self.started = 0
            self.stopped = 0
            self.announced = False
            FakeScanner.instances.append(self)

        def start(self) -> None:
            self.started += 1

        def stop(self) -> None:
            self.stopped += 1

        def sensors(self):
            self.polls += 1
            if self.polls <= appears_after_polls:
                return []
            if not self.announced and callable(self.sensorsChanged):
                self.announced = True
                self.sensorsChanged(self, list(bands))
            return list(bands)

        def create_sensor(self, info):
            if remaining_failures[0] > 0:
                remaining_failures[0] -= 1
                raise RuntimeError("BLE busy")
            sensor = FakeSensor(info)
            connected_sensors.append(sensor)
            return sensor

    FakeScanner.instances = []
    return FakeScanner


def _run_cli(scanner_class, argv):
    """Run the CLI against a fake SDK and return (exit code, tagged lines)."""
    captured = io.StringIO()
    with (
        mock.patch.object(cli, "_ensure_requirements"),
        mock.patch.object(cli, "_load_sdk_modules"),
        mock.patch.object(cli, "_validate_sdk_api_surface"),
        mock.patch.object(cli, "Scanner", scanner_class, create=True),
        mock.patch.object(cli, "SensorFamily", FakeFamilies, create=True),
        mock.patch.object(cli, "RETRY_DELAY_SECONDS", 0.0),
        redirect_stdout(captured),
    ):
        # Defaults first, so a test can override them by passing its own.
        exit_code = cli.main(["--no-osc", "--scan-seconds", "1", *argv])
    return exit_code, _tagged(captured.getvalue())


def _tagged(output: str) -> list[tuple[str, dict]]:
    lines: list[tuple[str, dict]] = []
    for line in output.splitlines():
        tag, _, payload = line.partition(" ")
        if payload.startswith("{"):
            try:
                lines.append((tag, json.loads(payload)))
            except json.JSONDecodeError:
                continue
    return lines


class ScanReliabilityTests(unittest.TestCase):
    def test_a_band_that_answers_after_the_scan_window_is_still_found(self) -> None:
        """A fixed window simply missed slow advertisers; a named band waits."""
        band = FakeInfo(serial="WANTED")
        scanner_class = _make_scanner(
            # ~3 seconds of 250ms slices: well past the 2s window below, and
            # inside the three-window allowance a named band is granted.
            appears_after_polls=12,
            bands=(band,),
        )
        _, lines = _run_cli(
            scanner_class,
            ["--scan-seconds", "2", "--serial-number", "WANTED", "--max-session-attempts", "1"],
        )

        self.assertTrue(any(tag == "DEVICE_SELECTED" for tag, _ in lines))
        self.assertTrue(any(tag == "CONNECTED" for tag, _ in lines))

    def test_an_absent_band_is_waited_for_instead_of_exiting(self) -> None:
        """Not finding the band is a wait, not a crash: the process keeps going."""
        scanner_class = _make_scanner(bands=(FakeInfo(serial="OTHER"),))
        exit_code, lines = _run_cli(
            scanner_class,
            ["--serial-number", "WANTED", "--max-session-attempts", "2"],
        )

        waiting = [payload for tag, payload in lines if tag == "WAITING"]
        self.assertTrue(waiting, "the operator must be told a retry is coming")
        self.assertIn("next_retry_at", waiting[0])
        self.assertEqual(exit_code, cli.EXIT_DEVICE_TARGET_MISSING)
        # Two attempts means two scans, each with its own fresh scanner.
        self.assertEqual(len(scanner_class.instances), 2)

    def test_every_scan_stops_the_scanner_it_started(self) -> None:
        """A scanner left running holds the adapter and poisons the next try."""
        scanner_class = _make_scanner(bands=())
        _run_cli(scanner_class, ["--serial-number", "WANTED", "--max-session-attempts", "2"])

        for scanner in scanner_class.instances:
            self.assertEqual(scanner.started, scanner.stopped)

    def test_announced_index_is_the_index_that_gets_connected(self) -> None:
        """The dashboard's 'Use this band' index must mean what it says.

        Candidates used to be announced with a position inside one callback
        batch while selection indexed a different list, so clicking a band
        could connect a neighbour.
        """
        bands = (FakeInfo(name="A", serial="AAA"), FakeInfo(name="B", serial="BBB"))
        scanner_class = _make_scanner(bands=bands)
        _, lines = _run_cli(
            scanner_class,
            ["--device-index", "1", "--max-session-attempts", "1"],
        )

        announced = {payload["index"]: payload["serial"] for tag, payload in lines if tag == "SCAN"}
        selected = [payload for tag, payload in lines if tag == "DEVICE_SELECTED"][0]
        self.assertEqual(announced[1], "BBB")
        self.assertEqual(selected["index"], 1)
        self.assertEqual([s.info.SerialNumber for s in scanner_class.connected], ["BBB"])

    def test_addresses_match_whatever_separators_were_typed(self) -> None:
        """`AA-BB-CC`, `aa:bb:cc` and bare hex all name the same band."""
        band = FakeInfo(address="AA:BB:CC:DD:EE:FF")
        for typed in ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff", "AABBCCDDEEFF"):
            with self.subTest(typed=typed):
                args = mock.Mock(
                    serial_number="",
                    device_address=typed,
                    device_name="",
                    device_index=0,
                )
                index, info, source = cli._select_sensor_info([band], args)
                self.assertEqual((index, info, source), (0, band, "device_address"))


class ReconnectLoopTests(unittest.TestCase):
    def test_a_failed_connection_is_retried_not_reported_as_a_crash(self) -> None:
        band = FakeInfo(serial="WANTED")
        scanner_class = _make_scanner(bands=(band,), connect_failures=1)
        _, lines = _run_cli(
            scanner_class,
            ["--serial-number", "WANTED", "--max-session-attempts", "2"],
        )

        tags = [tag for tag, _ in lines]
        self.assertIn("CONNECT_FAILED", tags)
        self.assertIn("WAITING", tags)
        # The second attempt got past the connect.
        self.assertIn("CONNECTED", tags)

    def test_bluetooth_being_off_is_not_retried(self) -> None:
        """Retrying cannot turn a radio on; say so once and stop."""

        class DeadScanner:
            def __init__(self, families) -> None:
                self.sensorsChanged = None

            def start(self) -> None:
                raise RuntimeError("Code 103: BLE adapter not found or disabled")

            def stop(self) -> None:
                return None

            def sensors(self):
                return []

        exit_code, lines = _run_cli(DeadScanner, ["--max-session-attempts", "0"])

        self.assertEqual(exit_code, cli.EXIT_BLE_UNAVAILABLE)
        self.assertNotIn("WAITING", [tag for tag, _ in lines])

    def test_a_stop_request_ends_the_loop_cleanly(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        args = mock.Mock(max_session_attempts=0)

        with mock.patch.object(cli, "_run_session") as session:
            exit_code = cli._run_until_stopped(args, None, stop_event)

        self.assertEqual(exit_code, cli.EXIT_OK)
        session.assert_not_called()


class BandReleaseTests(unittest.TestCase):
    """Ursache A: a band that is never disconnected refuses the next attempt."""

    def test_the_windows_stop_signal_is_handled(self) -> None:
        """The host sends CTRL_BREAK_EVENT, which arrives as SIGBREAK."""
        registered: list[int] = []

        def _record(signum, handler):
            registered.append(signum)

        scanner_class = _make_scanner(bands=())
        with mock.patch.object(cli.os_signal, "signal", _record):
            _run_cli(scanner_class, ["--serial-number", "WANTED", "--max-session-attempts", "1"])

        for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
            expected = getattr(cli.os_signal, name, None)
            if expected is not None:
                self.assertIn(expected, registered, f"{name} must reach the stop handler")

    def test_the_band_is_released_between_attempts(self) -> None:
        sensor = mock.MagicMock()
        cli._remember_active_sensor(sensor)
        cli._release_active_sensor()

        sensor.disconnect.assert_called_once()
        # Safe to call again: the interpreter's exit hook runs it too.
        cli._release_active_sensor()
        sensor.disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
