"""Package 5b preflight: storage capacity, never disk throughput.

See recording_capacity.py's own docstring for why a benchmark of the disk's
write speed is deliberately not part of this check.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.recording.recording_capacity import (
    MINIMUM_FREE_SPACE_RESERVE_BYTES,
    estimated_acquisition_bytes_per_second,
    evaluate_capacity,
    planned_duration_seconds,
)


STREAMS = {
    "brainbit": [
        {
            "channels": ["ch1", "ch2", "ch3", "ch4"],
            "channel_format": "float32",
            "nominal_rate_hz": 250,
        }
    ],
}
BACKUP = {"channel_names": ["brainbit.hr.valid", "brainbit.hr.status"], "rate_hz": 1}


class PlannedDurationSecondsTests(unittest.TestCase):
    def test_missing_settings_is_unknown(self) -> None:
        self.assertIsNone(planned_duration_seconds({}))

    def test_missing_study_settings_object_is_unknown(self) -> None:
        self.assertIsNone(planned_duration_seconds({"study_settings": None}))

    def test_valid_minutes_convert_to_seconds(self) -> None:
        self.assertEqual(
            planned_duration_seconds({"study_settings": {"planned_session_duration_minutes": 30}}),
            1800.0,
        )

    def test_zero_is_unknown_not_a_promise_of_no_data(self) -> None:
        self.assertIsNone(
            planned_duration_seconds({"study_settings": {"planned_session_duration_minutes": 0}})
        )

    def test_negative_is_unknown(self) -> None:
        self.assertIsNone(
            planned_duration_seconds({"study_settings": {"planned_session_duration_minutes": -5}})
        )

    def test_non_numeric_is_unknown(self) -> None:
        self.assertIsNone(
            planned_duration_seconds({"study_settings": {"planned_session_duration_minutes": "30"}})
        )

    def test_boolean_is_unknown(self) -> None:
        """bool is a subclass of int in Python; must not silently pass as 1 minute."""
        self.assertIsNone(
            planned_duration_seconds({"study_settings": {"planned_session_duration_minutes": True}})
        )


class EstimatedAcquisitionBytesPerSecondTests(unittest.TestCase):
    def test_sums_channel_count_times_format_size_times_rate(self) -> None:
        # 4 channels * 4 bytes (float32) * 250 Hz = 4000; backup: 2 channels * 8 bytes * 1 Hz = 16.
        self.assertEqual(estimated_acquisition_bytes_per_second(STREAMS, BACKUP), 4016.0)

    def test_no_streams_and_no_backup_is_zero(self) -> None:
        self.assertEqual(estimated_acquisition_bytes_per_second({}, {}), 0.0)

    def test_unrecognized_channel_format_falls_back_to_the_estimate(self) -> None:
        streams = {"fixture": [{"channels": ["marker"], "channel_format": "string", "nominal_rate_hz": 1}]}
        rate = estimated_acquisition_bytes_per_second(streams, {})
        self.assertGreater(rate, 0.0)

    def test_malformed_stream_entries_do_not_raise(self) -> None:
        streams = {"fixture": [None, {}, {"channels": "not-a-list"}]}
        self.assertEqual(estimated_acquisition_bytes_per_second(streams, {}), 0.0)


class EvaluateCapacityTests(unittest.TestCase):
    def test_missing_duration_fails_closed_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = evaluate_capacity(
                target_dir=Path(temp_dir),
                streams_by_source=STREAMS,
                backup_contract=BACKUP,
                config_data={},
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["known"])
        self.assertIn("planned_session_duration_minutes", result["reason"])

    def test_sufficient_space_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = evaluate_capacity(
                target_dir=Path(temp_dir),
                streams_by_source=STREAMS,
                backup_contract=BACKUP,
                config_data={"study_settings": {"planned_session_duration_minutes": 30}},
            )
        self.assertTrue(result["known"])
        self.assertTrue(result["ok"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["planned_duration_seconds"], 1800.0)
        self.assertGreaterEqual(result["required_bytes"], MINIMUM_FREE_SPACE_RESERVE_BYTES)

    def test_insufficient_space_fails_with_a_reason(self) -> None:
        class TinyDisk:
            free = 100  # bytes -- far below even the minimum reserve

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "study_runner.backend.services.recording.recording_capacity.shutil.disk_usage",
            return_value=TinyDisk(),
        ):
            result = evaluate_capacity(
                target_dir=Path(temp_dir),
                streams_by_source=STREAMS,
                backup_contract=BACKUP,
                config_data={"study_settings": {"planned_session_duration_minutes": 30}},
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["known"])
        self.assertIn("bytes free", result["reason"])


if __name__ == "__main__":
    unittest.main()
