from __future__ import annotations

import csv
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.delivery.csv_export_service import CsvExportError, write_backup_csv


class FixtureReader:
    def __init__(self, streams):
        self._streams = streams

    def read_streams(self, _path: Path):
        return self._streams


BACKUP_STREAM = {
    "stream_key": "backup.slowest_grid",
    "channels": ["brainbit.eeg.o1", "brainbit.eeg.o2", "mini_radar.vitals.heart_rate_bpm"],
    "timestamps": [10.0, 11.0, 12.0],
    "samples": [
        {"brainbit.eeg.o1": 1.5, "brainbit.eeg.o2": 2.5, "mini_radar.vitals.heart_rate_bpm": 72},
        {"brainbit.eeg.o1": 1.6, "brainbit.eeg.o2": None, "mini_radar.vitals.heart_rate_bpm": 73},
        {"brainbit.eeg.o1": 1.7, "brainbit.eeg.o2": 2.6, "mini_radar.vitals.heart_rate_bpm": 74},
    ],
}


class WriteBackupCsvTests(unittest.TestCase):
    def test_writes_one_row_per_timestamp_and_one_column_per_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "session.csv"
            details = write_backup_csv(
                Path(temp_dir) / "backup.xdf",
                target,
                sample_reader=FixtureReader([BACKUP_STREAM]),
            )

            self.assertEqual(details["row_count"], 3)
            self.assertEqual(details["channel_count"], 3)
            self.assertTrue(target.is_file())

            with target.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                set(rows[0].keys()),
                {"timestamp", "brainbit.eeg.o1", "brainbit.eeg.o2", "mini_radar.vitals.heart_rate_bpm"},
            )
            self.assertEqual(rows[0]["timestamp"], "10.0")
            self.assertEqual(rows[0]["brainbit.eeg.o1"], "1.5")
            # A missing sample stays empty rather than being invented.
            self.assertEqual(rows[1]["brainbit.eeg.o2"], "")

    def test_a_channel_less_boundary_stream_is_ignored_in_favor_of_the_data_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "session.csv"
            boundary_stream = {"stream_key": "boundaries", "channels": [], "timestamps": [], "samples": []}
            write_backup_csv(
                Path(temp_dir) / "backup.xdf",
                target,
                sample_reader=FixtureReader([boundary_stream, BACKUP_STREAM]),
            )

            with target.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)

    def test_no_channel_data_at_all_is_a_clear_error_not_an_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "session.csv"
            with self.assertRaises(CsvExportError):
                write_backup_csv(
                    Path(temp_dir) / "backup.xdf",
                    target,
                    sample_reader=FixtureReader([]),
                )
            self.assertFalse(target.exists())

    def test_creates_missing_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "nested" / "session.csv"
            write_backup_csv(
                Path(temp_dir) / "backup.xdf",
                target,
                sample_reader=FixtureReader([BACKUP_STREAM]),
            )
            self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
