from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.recording.backup import (
    STATUS_STALE,
    BackupChannel,
    BackupProjection,
    BackupSampler,
    choose_backup_rate,
    projections_from_manifest,
)


class RecordingBackupTests(unittest.TestCase):
    def test_slowest_positive_projection_rate_is_selected(self) -> None:
        projections = (
            BackupProjection("brainbit", "eeg", 250.0, (BackupChannel("f3", "f3"),)),
            BackupProjection("radar", "vitals", 10.0, (BackupChannel("heart_rate", "heart_rate"),)),
            BackupProjection("camera", "emotion", 1.0, (BackupChannel("valence", "valence"),)),
        )

        self.assertEqual(choose_backup_rate(projections), 1.0)
        sampler = BackupSampler(projections, start_monotonic=50.0)
        self.assertEqual(sampler.rate_hz, 1.0)
        self.assertEqual(sampler.next_deadline, 51.0)
        self.assertEqual(
            set(sampler.stream_metadata()["status_codes"]),
            {"missing", "valid", "stale", "degraded"},
        )

    def test_stale_projection_writes_nan_instead_of_last_value(self) -> None:
        projection = BackupProjection(
            "radar",
            "vitals",
            1.0,
            (
                BackupChannel("heart_rate", "heart_rate"),
                BackupChannel("breathing_rate", "breathing_rate"),
            ),
            stale_after_seconds=1.5,
        )
        sampler = BackupSampler((projection,), start_monotonic=0.0)
        sampler.update(
            "radar",
            "vitals",
            {"heart_rate": 72, "breathing_rate": 14},
            received_monotonic=0.5,
            sequence=9,
        )

        first = sampler.emit_due(1.0)[0]
        self.assertEqual(first.values["radar.vitals.heart_rate"], 72.0)
        self.assertEqual(first.values["radar.vitals.valid"], 1.0)

        later = sampler.emit_due(3.0)
        stale = later[-1]
        self.assertTrue(math.isnan(stale.values["radar.vitals.heart_rate"]))
        self.assertTrue(math.isnan(stale.values["radar.vitals.breathing_rate"]))
        self.assertEqual(stale.values["radar.vitals.valid"], 0.0)
        self.assertEqual(stale.values["radar.vitals.sequence"], 9.0)
        self.assertEqual(stale.values["radar.vitals.status"], STATUS_STALE)

    def test_v3_manifest_channel_mappings_are_grouped_by_lsl_stream(self) -> None:
        projections = projections_from_manifest(
            "sensor",
            {
                "rate_hz": 2,
                "stale_after_ms": 1500,
                "channels": [
                    {"output": "hr", "stream": "vitals", "channel": "heart_rate"},
                    {"output": "alpha", "stream": "eeg", "channel": "alpha_relative"},
                ],
            },
        )

        self.assertEqual({projection.stream_id for projection in projections}, {"vitals", "eeg"})
        self.assertTrue(all(projection.rate_hz == 2.0 for projection in projections))
        self.assertTrue(all(projection.stale_after_seconds == 1.5 for projection in projections))


if __name__ == "__main__":
    unittest.main()
