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
    adapter._preview["movement"].clear()
    adapter._preview["position"].clear()
    adapter._last_preview_epoch = 0.0
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

    def test_vitals_channel_stays_missing_until_the_hub_forwards_it(self) -> None:
        with mock.patch.object(adapter.time, "time", return_value=1.0):
            adapter._handle_sse_event('{"type":"sample","addr":"/sensor/presence","value":1.0,"t":1.0}')
            adapter._publish_combined_sample()
        self.assertIsNone(adapter._latest_state["latest"]["heartRate"])


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
