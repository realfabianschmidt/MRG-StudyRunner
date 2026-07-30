from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.clock_sync_service import ClockSyncService


class ClockSyncServiceTests(unittest.TestCase):
    def test_offset_samples_are_bounded_and_summarized_by_source(self) -> None:
        service = ClockSyncService(max_samples_per_source=3, clock=lambda: 1000.0)

        service.record_offset_sample(source_id="tablet-1", source_type="tablet", offset_ms=10, rtt_ms=40, sequence_number=1)
        service.record_offset_sample(source_id="tablet-1", source_type="tablet", offset_ms=20, rtt_ms=20, sequence_number=2)
        service.record_offset_sample(source_id="tablet-1", source_type="tablet", offset_ms=30, rtt_ms=30, sequence_number=3)
        service.record_offset_sample(source_id="tablet-1", source_type="tablet", offset_ms=40, rtt_ms=10, sequence_number=4)

        source = service.source_summary("tablet-1")
        self.assertIsNotNone(source)
        self.assertEqual(source["sample_count"], 3)
        self.assertEqual(source["latest_offset_ms"], 40)
        self.assertEqual(source["median_offset_ms"], 30)
        self.assertEqual(source["median_rtt_ms"], 20)

    def test_invalid_offsets_are_ignored(self) -> None:
        service = ClockSyncService()

        sample = service.record_offset_sample(source_id="tablet-1", source_type="tablet", offset_ms="nan")

        self.assertIsNone(sample)
        self.assertEqual(service.summary()["sources"], {})

    def test_server_exchange_records_observing_sample_until_offset_arrives(self) -> None:
        service = ClockSyncService(clock=lambda: 1000.0)

        service.record_server_exchange(
            source_id="tablet-1",
            source_type="tablet",
            client_send_ms=50,
            server_receive_ms=100,
            server_send_ms=101.5,
        )

        source = service.summary()["sources"]["tablet-1"]
        self.assertEqual(source["status"], "observing")
        self.assertEqual(source["last_server_processing_ms"], 1.5)


if __name__ == "__main__":
    unittest.main()
