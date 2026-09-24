from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.sensors.am_hub import adapter


class FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def iter_lines(self, decode_unicode: bool = True):
        return iter(self._lines)


def _reset_adapter_state() -> None:
    adapter._config = {}
    adapter._running = False
    adapter._history.clear()
    adapter._topics.clear()
    adapter._last_topic_update_epoch = None
    adapter._clear_preview()
    adapter._latest_state = {
        "status": "not_configured",
        "latest": {},
        "last_message": "AM Hub adapter has not been configured.",
    }


class SseEventHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_adapter_state()
        adapter._config = {"enabled": True, "base_url": "http://hub", "lsl_enabled": False}
        adapter._running = True

    def test_init_event_seeds_topics_from_last_sample(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=1.0):
            adapter._handle_sse_event(
                '{"type":"init","t":1.0,"topics":{'
                '"/sensor/presence":{"samples":[[0.9,0.0],[1.0,1.0]]},'
                '"/sensor/personX":{"samples":[[1.0,42.5]]}'
                '}}'
            )
            adapter._publish_combined_sample()
        sample = adapter._latest_state["latest"]
        self.assertEqual(sample["presence"], 1.0)
        self.assertEqual(sample["personX"], 42.5)

    def test_sample_event_updates_a_single_topic(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=2.0):
            adapter._handle_sse_event('{"type":"sample","addr":"/sensor/presMoveEnergy","value":37.5,"t":2.0}')
            adapter._publish_combined_sample()
        self.assertEqual(adapter._latest_state["latest"]["moveEnergy"], 37.5)

    def test_unknown_topic_is_ignored_without_error(self) -> None:
        adapter._handle_sse_event('{"type":"sample","addr":"/sensor/unrelated","value":1.0,"t":2.0}')
        adapter._publish_combined_sample()
        self.assertEqual(adapter._topics, {})

    def test_tick_event_carries_no_value_and_is_a_no_op(self) -> None:
        adapter._handle_sse_event('{"type":"tick","t":3.0,"topics":{"/sensor/presence":{"rate":9.5}}}')
        self.assertEqual(adapter._topics, {})

    def test_malformed_json_is_ignored_without_raising(self) -> None:
        adapter._handle_sse_event("{not json")  # must not raise

    def test_read_sse_events_parses_multiple_blocks_and_skips_comments(self) -> None:
        lines = [
            'data: {"type":"sample","addr":"/sensor/presence","value":1.0,"t":5.0}',
            "",
            ": heartbeat, ignored",
            'data: {"type":"sample","addr":"/sensor/presMoveEnergy","value":10.0,"t":5.1}',
            "",
        ]
        with mock.patch.object(adapter.time, "time", return_value=5.1):
            adapter._read_sse_events(FakeResponse(lines))
            adapter._publish_combined_sample()
        sample = adapter._latest_state["latest"]
        self.assertEqual(sample["presence"], 1.0)
        self.assertEqual(sample["moveEnergy"], 10.0)

    def test_a_channel_the_hub_never_sent_stays_missing(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=1.0):
            adapter._handle_sse_event('{"type":"sample","addr":"/sensor/presence","value":1.0,"t":1.0}')
            adapter._publish_combined_sample()
        self.assertIsNone(adapter._latest_state["latest"]["heartRate"])

    def test_hub_clock_offset_does_not_affect_freshness(self) -> None:
        # Hub clock 1000 s behind this machine: values must still count as fresh.
        with mock.patch.object(adapter.time, "time", return_value=5000.0):
            adapter._handle_sse_event('{"type":"sample","addr":"/sensor/presMoveEnergy","value":5.0,"t":4000.0}')
            adapter._publish_combined_sample()
        self.assertEqual(adapter._latest_state["latest"]["moveEnergy"], 5.0)


class V2EventTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_adapter_state()
        adapter._config = {"enabled": True, "base_url": "http://hub", "lsl_enabled": False}
        adapter._running = True
        adapter._hub_rtts.clear()
        adapter._hub_boards.clear()
        adapter._frame_counts.clear()
        adapter._seq_gaps.clear()
        adapter._last_seq.clear()
        adapter._hub_dropped_events = 0

    def _frame(self, seq: int, values: dict, device: str = "radar_hub", role: str = "radar") -> str:
        import json
        return json.dumps({"type": "frame", "device": device, "role": role, "seq": seq,
                           "t": 1.0, "values": values, "server_now": 1.0})

    def test_frame_updates_all_values_of_the_packet_together(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=10.0):
            adapter._handle_sse_event(self._frame(1, {
                "/sensor/personX": 12.0, "/sensor/personY": 34.0, "/sensor/t1res": 320.0,
                "/sensor/presDetDist": 1500.0}))
            adapter._publish_combined_sample()
        latest = adapter._latest_state["latest"]
        self.assertEqual((latest["personX"], latest["personY"]), (12.0, 34.0))
        self.assertEqual(latest["t1res"], 320.0)
        self.assertEqual(latest["presDetDist"], 1500.0)

    def test_sequence_gaps_are_counted(self) -> None:
        for seq in (1, 2, 5, 6):
            adapter._handle_sse_event(self._frame(seq, {"/sensor/personX": 1.0}))
        self.assertEqual(adapter._seq_gaps["radar_hub"], 2)
        self.assertEqual(adapter._frame_counts["radar_hub"], 4)

    def test_valve_topics_and_scene_state_reach_the_valves_stream(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=10.0):
            adapter._handle_sse_event(self._frame(1, {"/solenoid/CH3": 1.0}, "solenoid_hub", "solenoid"))
            adapter._handle_sse_event('{"type":"valves","channels":[0,0,0,1,0,0,0,0],"scene_active":true,"server_now":1.0}')
            adapter._publish_combined_sample()
        latest = adapter._latest_state["latest"]
        self.assertEqual(latest["ch3"], 1.0)
        self.assertEqual(latest["sceneActive"], 1.0)

    def test_status_yields_per_board_latency(self) -> None:
        adapter._hub_rtts.extend([4.0, 4.0, 4.0])
        status = ('{"type":"status","server_now":1.0,"devices":{"radar_hub":{"role":"radar","connected":true,'
                  '"transport":"ble","rssi":-60,"rate_hz":10.0,"gap_count":3,"link_rtt_ms":20.0,"hub_latency_ms":1.5}}}')
        with mock.patch.object(adapter.time, "time", return_value=10.0):
            adapter._handle_sse_event(status)
            adapter._publish_combined_sample()
        latest = adapter._latest_state["latest"]
        self.assertEqual(latest["radarLatencyMs"], 20.0 / 2 + 1.5 + 4.0 / 2)
        self.assertEqual(latest["radarTransport"], 1.0)
        self.assertEqual(latest["radarLost"], 3.0)
        self.assertEqual(latest["hubRttMs"], 4.0)
        self.assertEqual(adapter.get_status()["hub_boards"]["radar"]["latency_ms"], 13.5)

    def test_hub_gap_events_are_counted(self) -> None:
        adapter._handle_sse_event('{"type":"gap","dropped":7,"server_now":1.0}')
        self.assertEqual(adapter.get_status()["data_quality"]["hub_dropped_events"], 7)

    def test_hub_state_does_not_count_as_sensor_activity(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=10.0):
            adapter._handle_sse_event('{"type":"valves","channels":[],"scene_active":false,"server_now":1.0}')
        self.assertIsNone(adapter._last_topic_update_epoch)


class ConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_adapter_state()

    def test_falls_back_to_v1_when_the_hub_has_no_v2(self) -> None:
        requested = []

        class Response:
            def __init__(self, status: int) -> None:
                self.status_code = status

            def raise_for_status(self) -> None:
                pass

            def iter_lines(self, decode_unicode: bool = True):
                adapter._running = False     # end the loop after the v1 connect
                return iter([])

            def close(self) -> None:
                pass

        def fake_get(url, **_kwargs):
            requested.append(url)
            return Response(404 if url.endswith("/api/v2/stream") else 200)

        adapter._config = {"enabled": True, "base_url": "http://hub", "reconnect_delay_seconds": 1.0,
                           "auto_reconnect": False}
        adapter._running = True
        adapter._generation = 1
        with mock.patch("requests.get", side_effect=fake_get):
            adapter._sse_loop(1)
        self.assertEqual(requested, ["http://hub/api/v2/stream", "http://hub/api/v1/stream"])
        self.assertEqual(adapter._api_version, "v1")

    def test_threads_of_an_old_run_stop_after_restart(self) -> None:
        adapter._running = True
        adapter._generation = 5
        self.assertTrue(adapter._alive(5))
        adapter._generation = 6
        self.assertFalse(adapter._alive(5))


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_adapter_state()
        adapter._config = {"enabled": True, "base_url": "http://hub", "lsl_enabled": False}

    def test_a_sample_with_data_is_marked_valid(self) -> None:
        adapter.ingest_sample({"moveEnergy": 12.0, "personX": 5.0}, source="test")
        movement = adapter._preview["movement"][-1]
        self.assertEqual(movement["validity"], "valid")
        self.assertEqual(movement["values"]["moveEnergy"], 12.0)

    def test_a_sample_with_no_relevant_channel_is_marked_uncertain(self) -> None:
        adapter.ingest_sample({"heartRate": 70.0}, source="test")
        movement = adapter._preview["movement"][-1]
        self.assertEqual(movement["validity"], "uncertain")

    def test_preview_updates_are_throttled_to_about_once_a_second(self) -> None:
        adapter.ingest_sample({"moveEnergy": 1.0}, source="test")
        adapter.ingest_sample({"moveEnergy": 2.0}, source="test")  # same instant, throttled away
        self.assertEqual(len(adapter._preview["movement"]), 1)

    def test_vitals_preview_carries_heart_and_breath_rate(self) -> None:
        adapter.ingest_sample({"heartRate": 70.0, "breathRate": 14.0}, source="test")
        vitals = adapter._preview["vitals"][-1]
        self.assertEqual(vitals["validity"], "valid")
        self.assertEqual(vitals["values"], {"heartRate": 70.0, "breathRate": 14.0})

    def test_start_clears_every_preview_so_no_session_sees_the_previous_one(self) -> None:
        adapter.ingest_sample({"moveEnergy": 1.0, "personX": 2.0, "heartRate": 70.0}, source="test")
        with mock.patch.object(adapter, "_sse_loop"), mock.patch.object(adapter, "_publish_loop"),                 mock.patch.object(adapter, "_ping_loop"):
            adapter.start()
        try:
            self.assertTrue(all(not points for points in adapter._preview.values()))
            self.assertEqual(adapter._last_preview_epoch, 0.0)
        finally:
            adapter.stop()


class StalenessTests(unittest.TestCase):
    """A fixed-rate publisher must not carry a stale value forward as live,
    and get_status() must be able to detect a stalled hub connection even
    while it keeps publishing on schedule."""

    def setUp(self) -> None:
        _reset_adapter_state()
        adapter._config = {
            "enabled": True, "base_url": "http://hub", "lsl_enabled": False,
            "data_timeout_seconds": 5.0,
        }

    def test_a_topic_older_than_the_staleness_window_publishes_as_missing(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=1000.0):
            adapter._update_topic("/sensor/presMoveEnergy", 42.0, 1000.0 - adapter.TOPIC_STALE_SECONDS - 1.0)
            adapter._publish_combined_sample()
        self.assertIsNone(adapter._latest_state["latest"]["moveEnergy"])

    def test_a_fresh_topic_still_publishes_its_value(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=1000.0):
            adapter._update_topic("/sensor/presMoveEnergy", 42.0, 999.5)
            adapter._publish_combined_sample()
        self.assertEqual(adapter._latest_state["latest"]["moveEnergy"], 42.0)

    def test_status_reports_stale_once_topics_stop_updating_even_while_publishing_continues(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=1000.0):
            adapter._update_topic("/sensor/presence", 1.0, 1000.0)
        adapter._running = True

        # 10s later, well past the 5s timeout -- but the publisher is still
        # ticking (ingest_sample called), as it would every 100ms in reality.
        with mock.patch.object(adapter.time, "time", return_value=1010.0):
            adapter.ingest_sample({"presence": None}, source="test")
            status = adapter.get_status()

        self.assertEqual(status["status"], "stale")

    def test_status_stays_fresh_while_topics_keep_updating(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=1000.0):
            adapter._update_topic("/sensor/presence", 1.0, 1000.0)
            adapter.ingest_sample({"presence": 1.0}, source="test")
            status = adapter.get_status()
        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["seconds_since_last_activity"], 0.0)


class IntervalSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_adapter_state()
        adapter._config = {"enabled": True, "base_url": "http://hub", "lsl_enabled": False}

    def test_empty_history_reports_unavailable(self) -> None:
        summary = adapter.get_interval_summary(0.0, 1.0)
        self.assertFalse(summary["available"])
        self.assertEqual(summary["sample_count"], 0)

    def test_averages_only_present_channels(self) -> None:
        adapter.ingest_sample({"presence": 1.0, "moveEnergy": 10.0}, source="test")
        epoch = adapter._history[-1]["_epoch"]
        adapter.ingest_sample({"presence": 0.0, "moveEnergy": 20.0}, source="test")
        summary = adapter.get_interval_summary(epoch - 1.0, epoch + 10.0)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["avg_presence"], 0.5)
        self.assertEqual(summary["avg_move_energy"], 15.0)
        self.assertIsNone(summary["avg_heart_rate"])


class LslPublishTests(unittest.TestCase):
    """Missing channels must publish as NaN, never a misleading 0.0."""

    def setUp(self) -> None:
        _reset_adapter_state()

    def test_missing_channel_becomes_nan_not_zero(self) -> None:
        class FakeOutlet:
            def __init__(self) -> None:
                self.pushed: list[float] = []

            def push_sample(self, values: list[float]) -> None:
                self.pushed = values

        outlet = FakeOutlet()
        adapter._lsl_outlets = {"VITALS": outlet}
        adapter._push_lsl_values("VITALS", {"heartRate": 72.0}, adapter.STREAM_CHANNELS["vitals"])

        self.assertEqual(outlet.pushed[0], 72.0)
        self.assertTrue(all(math.isnan(value) for value in outlet.pushed[1:]))


if __name__ == "__main__":
    unittest.main()
