from __future__ import annotations

import json
import struct
import math
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from support.fake_lsl import FakePylsl  # noqa: E402
from study_runner.plugins.sensors.mr60_mini_radar import adapter  # noqa: E402


class MR60BleDecoderTests(unittest.TestCase):
    def setUp(self) -> None:
        adapter._reset_ble_stats()

    def test_decode_ble_packet_scales_values_and_flags(self) -> None:
        packet = adapter.BLE_PACKET.pack(
            1,
            0x07,
            42,
            123456,
            721,
            134,
            1825,
            120,
            -240,
            12,
        )

        sample = adapter._decode_ble_packet(packet)

        self.assertIsNotNone(sample)
        self.assertEqual(sample["sequence_number"], 42)
        self.assertTrue(sample["valid"])
        self.assertTrue(sample["stabilized"])
        self.assertTrue(sample["present"])
        self.assertEqual(sample["heartRate"], 72.1)
        self.assertEqual(sample["breathRate"], 13.4)
        self.assertEqual(sample["distance"], 182.5)
        self.assertEqual(sample["heartPhase"], 1.2)
        self.assertEqual(sample["breathPhase"], -2.4)

    def test_decode_ble_packet_tracks_missing_values_and_sequence_gaps(self) -> None:
        first = adapter.BLE_PACKET.pack(
            1,
            0x01,
            10,
            1000,
            adapter.MISSING_INT16,
            120,
            adapter.MISSING_INT16,
            adapter.MISSING_INT16,
            adapter.MISSING_INT16,
            adapter.MISSING_INT16,
        )
        second = adapter.BLE_PACKET.pack(
            1,
            0x01,
            13,
            1300,
            800,
            130,
            1000,
            0,
            0,
            0,
        )

        first_sample = adapter._decode_ble_packet(first)
        second_sample = adapter._decode_ble_packet(second)

        self.assertIsNone(first_sample["heartRate"])
        self.assertIsNone(first_sample["distance"])
        self.assertEqual(second_sample["dropped_since_previous"], 2)
        self.assertEqual(second_sample["total_dropped"], 2)
        self.assertEqual(second_sample["device_interval_ms"], 300)


def _reset_adapter() -> None:
    adapter._config = {}
    adapter._running = False
    adapter._generation = 0
    adapter._reader_thread = None
    adapter._serial_connection = None
    adapter._streams.use_backend(FakePylsl())
    adapter._history.clear()
    adapter._stop_event.clear()
    adapter._latest_state = {"status": "not_configured", "latest": {}, "last_message": ""}


class FakeSerial:
    """Serial port stand-in: yields the given lines, then raises (unplugged)."""

    def __init__(self, lines: list[bytes]) -> None:
        self.lines = list(lines)
        self.closed = False

    def readline(self) -> bytes:
        if self.closed or not self.lines:
            raise OSError("device unplugged")
        return self.lines.pop(0)

    def close(self) -> None:
        self.closed = True


class MR60LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_adapter()
        self.addCleanup(adapter.stop)
        self.addCleanup(_reset_adapter)

    def test_not_configured_disabled_and_missing_port_do_not_start_a_reader(self) -> None:
        self.assertEqual(adapter.start()["status"], "not_configured")
        adapter.initialize(enabled=False, port="COM3")
        self.assertEqual(adapter.start()["status"], "disabled")
        adapter._config["enabled"] = True
        adapter._config["port"] = ""
        self.assertEqual(adapter.start()["status"], "waiting")
        self.assertIsNone(adapter._reader_thread)

    def test_serial_lines_become_samples_and_bad_lines_are_skipped(self) -> None:
        port = FakeSerial([b'{"heartRate": 71.5, "breathRate": 14, "distance": 80}\n', b"garbage\n", b"\n"])
        adapter._config = {"enabled": True, "port": "COM3", "auto_reconnect": False}
        adapter._serial_connection = port
        adapter._running = True
        adapter._generation = 1
        adapter._read_loop(1)
        self.assertEqual(len(adapter._history), 1)
        self.assertEqual(adapter._history[0]["heartRate"], 71.5)
        self.assertEqual(adapter._history[0]["source"], "serial")
        self.assertFalse(adapter._running)  # no auto-reconnect: the unplugged port ends the run

    def test_a_failed_read_reconnects_when_auto_reconnect_is_on(self) -> None:
        ports = [FakeSerial([b'{"heartRate": 60}\n']), FakeSerial([b'{"heartRate": 61}\n'])]

        def open_next() -> None:
            if not ports:
                adapter._running = False  # end the test after two connections
                return
            adapter._serial_connection = ports.pop(0)

        adapter._config = {"enabled": True, "port": "COM3", "auto_reconnect": True, "reconnect_delay": 0.0}
        adapter._running = True
        adapter._generation = 1
        with mock.patch.object(adapter, "_open_serial_connection", side_effect=open_next):
            adapter._read_loop(1)
        self.assertEqual([sample["heartRate"] for sample in adapter._history], [60.0, 61.0])

    def test_start_clears_the_previous_participants_samples(self) -> None:
        adapter.ingest_sample({"heartRate": 70})
        adapter._config = {"enabled": True, "port": "COM3", "connection_type": "serial"}
        release = threading.Event()
        with mock.patch.object(adapter, "_read_loop", side_effect=lambda generation: release.wait(1)):
            adapter.start()
            self.assertEqual(len(adapter._history), 0)
            release.set()

    def test_a_reader_of_an_old_run_stops_after_restart(self) -> None:
        adapter._running = True
        adapter._generation = 3
        self.assertTrue(adapter._alive(3))
        adapter.stop()
        adapter._running = True  # a new run started
        self.assertFalse(adapter._alive(3))

    def test_status_turns_stale_when_data_stops(self) -> None:
        adapter._config = {"enabled": True, "port": "COM3", "data_timeout_seconds": 5.0}
        adapter._running = True
        with mock.patch.object(adapter.time, "time", return_value=100.0):
            adapter.ingest_sample({"heartRate": 70})
        with mock.patch.object(adapter.time, "time", return_value=103.0):
            self.assertEqual(adapter.get_status()["status"], "connected")
        with mock.patch.object(adapter.time, "time", return_value=110.0):
            self.assertEqual(adapter.get_status()["status"], "stale")


class MR60RecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_adapter()
        self.addCleanup(_reset_adapter)

    def _open(self) -> FakePylsl:
        lsl = FakePylsl(clock=500.0)
        adapter._streams.use_backend(lsl)
        adapter._config = {"lsl_enabled": True}
        self.assertTrue(adapter._streams.open_all())
        return lsl

    def _rows(self, lsl: FakePylsl, key: str) -> list[dict]:
        contract = adapter.STREAM_CONTRACTS[key]
        return [dict(zip(contract["channels"], row)) for row in lsl.outlet(contract["source_id"]).rows]

    def test_a_missing_value_reaches_lsl_as_nan_never_as_zero(self) -> None:
        lsl = self._open()
        adapter.ingest_sample({"heartRate": 70, "breathRate": None})
        vitals = self._rows(lsl, "vitals")[0]
        self.assertEqual(vitals["heartRate"], 70.0)
        self.assertTrue(math.isnan(vitals["breathRate"]))
        phases = self._rows(lsl, "phases")[0]
        self.assertTrue(all(math.isnan(phases[name]) for name in ("heartPhase", "breathPhase", "totalPhase")))

    def test_a_ble_packet_records_its_flags_counter_and_device_clock_at_arrival(self) -> None:
        lsl = self._open()
        packet = struct.pack("<BBHIhhhhhh", 1, 0x07, 42, 123456, 721, 155, 1234, 10, 20, 30)
        lsl.clock = 777.5
        adapter._handle_ble_notification(None, bytearray(packet))
        vitals = self._rows(lsl, "vitals")[0]
        self.assertEqual((vitals["flags"], vitals["seq"], vitals["device_ms"]), (7.0, 42.0, 123456.0))
        self.assertEqual(vitals["distance"], 123.4)  # centimetres, as the firmware sends them
        phases = self._rows(lsl, "phases")[0]
        self.assertEqual((phases["seq"], phases["device_ms"]), (42.0, 123456.0))
        stamps = lsl.outlet("study_runner.mr60.vitals").timestamps
        self.assertEqual(stamps, [777.5])

    def test_the_streams_follow_the_manifest_and_the_contract(self) -> None:
        vitals = adapter.STREAM_CONTRACTS["vitals"]
        self.assertEqual(vitals["channel_format"], "double64")  # u32 millis and u16 counters stay exact
        self.assertEqual(vitals["channel_units"][vitals["channels"].index("distance")], "centimetre")
        self.assertEqual(vitals["sequence_channel"], "seq")
        self.assertEqual(vitals["timing"]["timestamp_source"], "host_arrival")
        manifest = json.loads((Path(adapter.__file__).parent / "manifest.json").read_text(encoding="utf-8"))
        outputs = [channel["output"] for channel in manifest["capabilities"]["backup_projection"]["channels"]]
        self.assertIn("distance_cm", outputs)
        self.assertNotIn("distance_m", outputs)

    def test_interval_summary_reports_losses_inside_the_window(self) -> None:
        for epoch, dropped in ((10.0, 2), (11.0, 3), (12.0, 7)):
            with mock.patch.object(adapter.time, "time", return_value=epoch):
                adapter.ingest_sample({"heartRate": 60, "total_dropped": dropped})
        summary = adapter.get_interval_summary(9.0, 13.0)
        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["dropped_in_interval"], 5)
        self.assertEqual(summary["avg_heart_rate"], 60.0)


if __name__ == "__main__":
    unittest.main()
