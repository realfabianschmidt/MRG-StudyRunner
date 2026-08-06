from __future__ import annotations

import math
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.studies.card_summary_service import CardSummaryBuilder, CardSummaryError


class FixtureReader:
    def read_streams(self, _path: Path):
        return [
            {
                "stream_key": "sensor.metrics",
                "plugin_key": "fixture_sensor",
                "name": "Fixture metrics",
                "nominal_rate_hz": 2,
                "channel_types": {"active": "boolean", "mood": "categorical"},
                "timestamps": [10.0, 10.5, 11.0, 11.5, 12.0],
                "samples": [
                    {"value": 1.0, "active": True, "mood": "calm", "valid": True, "sequence": 1},
                    {"value": 2.0, "active": False, "mood": "calm", "valid": True, "sequence": 2},
                    {"value": 99.0, "active": True, "mood": "alert", "valid": False, "sequence": 4},
                    {"value": 4.0, "active": True, "mood": "alert", "valid": True, "sequence": 5},
                    {"value": 100.0, "active": True, "mood": "outside", "valid": True, "sequence": 6},
                ],
            }
        ]


class CardSummaryBuilderTests(unittest.TestCase):
    def test_half_open_window_statistics_and_quality_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "session.xdf"
            merged.write_bytes(b"fixture")
            summary = CardSummaryBuilder(FixtureReader()).build(
                merged,
                [
                    {
                        "event_id": "event-1",
                        "question_index": 2,
                        "question_type": "stimulus",
                        "client_start_trigger_epoch_ms": 10_000,
                        "client_stop_trigger_epoch_ms": 12_000,
                    }
                ],
                session_id="session-1",
            )

        self.assertEqual(summary["window_semantics"], "half_open_[start,end)")
        card = summary["cards"][0]
        stream = card["streams"]["sensor.metrics"]
        self.assertEqual(stream["count"], 4, "sample exactly at end must be excluded")
        self.assertEqual(stream["valid_count"], 3)
        self.assertEqual(stream["expected_count"], 4)
        self.assertEqual(stream["coverage"], 1.0)
        self.assertEqual(stream["missing_count"], 0)
        self.assertEqual(stream["drop_count"], 1)
        self.assertEqual(stream["max_gap_seconds"], 0.5)

        numeric = stream["channels"]["value"]
        self.assertEqual(numeric["valid_count"], 3)
        self.assertAlmostEqual(numeric["mean"], 7 / 3)
        self.assertEqual(numeric["min"], 1.0)
        self.assertEqual(numeric["max"], 4.0)
        self.assertAlmostEqual(numeric["stddev"], math.sqrt(7 / 3))

        boolean = stream["channels"]["active"]
        self.assertEqual(boolean["kind"], "boolean")
        self.assertAlmostEqual(boolean["mean"], 2 / 3)
        categorical = stream["channels"]["mood"]
        self.assertEqual(categorical["frequencies"], {"alert": 1, "calm": 2})
        self.assertEqual(categorical["mode"], "calm")

    def test_invalid_timestamp_contract_is_rejected(self) -> None:
        class BrokenReader:
            def read_streams(self, _path):
                return [{"timestamps": [1.0], "samples": []}]

        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "session.xdf"
            merged.write_bytes(b"fixture")
            with self.assertRaises(CardSummaryError):
                CardSummaryBuilder(BrokenReader()).build(merged, [])

    def test_marker_event_ids_define_windows_in_raw_xdf_clock_domain(self) -> None:
        class MarkerReader:
            def read_streams(self, _path):
                return [
                    {
                        "stream_key": "markers",
                        "timestamps": [100.0, 102.0],
                        "samples": [
                            {"event": "stimulus_active_start|event_id=start-1"},
                            {"event": "stimulus_active_stop|event_id=stop-1"},
                        ],
                    },
                    {
                        "stream_key": "sensor",
                        "nominal_rate_hz": 1,
                        "timestamps": [99.0, 100.0, 101.0, 102.0],
                        "samples": [{"value": 0}, {"value": 1}, {"value": 2}, {"value": 3}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "session.xdf"
            merged.write_bytes(b"fixture")
            summary = CardSummaryBuilder(MarkerReader()).build(
                merged,
                [
                    {
                        "question_index": 1,
                        "question_type": "stimulus",
                        "start_event_id": "start-1",
                        "stop_event_id": "stop-1",
                        # Deliberately incompatible Unix timestamps. Marker
                        # windows must win over this fallback domain.
                        "client_start_trigger_epoch_ms": 1_700_000_000_000,
                        "client_stop_trigger_epoch_ms": 1_700_000_002_000,
                    }
                ],
                session_id="session-marker",
            )

        card = summary["cards"][0]
        self.assertEqual(card["time_source"], "xdf_marker_event_ids")
        self.assertEqual(card["start_epoch"], 100.0)
        self.assertEqual(card["end_epoch"], 102.0)
        self.assertEqual(card["streams"]["sensor"]["channels"]["value"]["mean"], 1.5)

    def test_recording_summary_never_falls_back_to_browser_epoch_without_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "session.xdf"
            merged.write_bytes(b"fixture")
            with self.assertRaises(CardSummaryError):
                CardSummaryBuilder(FixtureReader()).build(
                    merged,
                    [
                        {
                            "shown_event_id": "shown-1",
                            "answered_event_id": "answered-1",
                            "client_start_trigger_epoch_ms": 10_000,
                            "client_stop_trigger_epoch_ms": 12_000,
                        }
                    ],
                    require_xdf_markers=True,
                    required_marker_event_ids=["study-end-1"],
                )

    def test_recovery_segments_are_summarized_as_one_logical_stream(self) -> None:
        class SegmentedReader:
            def read_streams(self, _path):
                common = {
                    "plugin_key": "brainbit",
                    "source_id": "study_runner.brainbit.eeg",
                    "name": "BrainBit EEG",
                    "nominal_rate_hz": 2,
                    "channel_types": {"value": "numeric"},
                }
                return [
                    {
                        **common,
                        "stream_key": "merged-stream-11",
                        "stream_id": "11",
                        "timestamps": [10.0, 10.5],
                        "samples": [
                            {"value": 1.0, "sequence": 1},
                            {"value": 2.0, "sequence": 2},
                        ],
                    },
                    {
                        **common,
                        "stream_key": "merged-stream-37",
                        "stream_id": "37",
                        "timestamps": [11.0, 11.5],
                        "samples": [
                            {"value": 3.0, "sequence": 3},
                            {"value": 4.0, "sequence": 4},
                        ],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "session.xdf"
            merged.write_bytes(b"fixture")
            summary = CardSummaryBuilder(SegmentedReader()).build(
                merged,
                [
                    {
                        "event_id": "event-segmented",
                        "client_start_trigger_epoch_ms": 10_000,
                        "client_stop_trigger_epoch_ms": 12_000,
                    }
                ],
            )

        self.assertEqual(summary["source_stream_count"], 2)
        self.assertEqual(summary["stream_count"], 1)
        streams = summary["cards"][0]["streams"]
        self.assertEqual(len(streams), 1)
        logical = next(iter(streams.values()))
        self.assertEqual(logical["segment_count"], 2)
        self.assertEqual(logical["segment_stream_ids"], ["11", "37"])
        self.assertEqual(logical["count"], 4)
        self.assertEqual(logical["expected_count"], 4)
        self.assertEqual(logical["coverage"], 1.0)
        self.assertEqual(logical["channels"]["value"]["mean"], 2.5)

    def test_recovery_segments_with_incompatible_metadata_are_rejected(self) -> None:
        class IncompatibleReader:
            def read_streams(self, _path):
                return [
                    {
                        "stream_key": "one",
                        "plugin_key": "sensor",
                        "source_id": "stable-source",
                        "name": "signal",
                        "nominal_rate_hz": 10,
                        "timestamps": [1.0],
                        "samples": [{"a": 1.0}],
                    },
                    {
                        "stream_key": "two",
                        "plugin_key": "sensor",
                        "source_id": "stable-source",
                        "name": "signal",
                        "nominal_rate_hz": 10,
                        "timestamps": [2.0],
                        "samples": [{"b": 2.0}],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "session.xdf"
            merged.write_bytes(b"fixture")
            with self.assertRaises(CardSummaryError):
                CardSummaryBuilder(IncompatibleReader()).build(merged, [])


if __name__ == "__main__":
    unittest.main()
