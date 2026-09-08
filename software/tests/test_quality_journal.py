"""Package 5c: quality and timing observed while recording, not afterwards.

The point of the package is that an *aborted* session still leaves QC
behind, so these tests care as much about what survives a crash as about
the arithmetic.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.quality_journal import (
    EVENT_CLOCK_JUMP,
    EVENT_GAP,
    EVENT_SUMMARY,
    EVENT_TIMESTAMP_REGRESSION,
    QUALITY_JOURNAL_SCHEMA,
    StreamQualityObserver,
    WallClockJumpDetector,
    summarize_quality_journal,
)
from study_runner.data_core.worker.session_journals import (
    QUALITY_JOURNAL_FILENAME,
    TIMING_JOURNAL_FILENAME,
    SessionJournalWriter,
)


def _observer(rate_hz: float = 10.0) -> StreamQualityObserver:
    return StreamQualityObserver(plugin_key="fixture", stream_key="values", nominal_rate_hz=rate_hz)


class StreamQualityObserverTests(unittest.TestCase):
    def test_an_even_stream_reports_no_events(self) -> None:
        observer = _observer()
        events = observer.observe_chunk([1.0, 1.1, 1.2, 1.3], monotonic=5.0)
        self.assertEqual(events, [])
        self.assertEqual(observer.sample_count, 4)

    def test_effective_rate_is_measured_not_taken_from_the_manifest(self) -> None:
        observer = _observer(rate_hz=10.0)
        # Four samples 0.2s apart is really 5 Hz, whatever the manifest says.
        observer.observe_chunk([0.0, 0.2, 0.4, 0.6], monotonic=1.0)
        self.assertAlmostEqual(observer.effective_rate_hz(), 5.0)
        self.assertEqual(observer.nominal_rate_hz, 10.0)

    def test_a_long_delay_is_reported_as_a_gap(self) -> None:
        observer = _observer(rate_hz=10.0)  # expects 0.1s spacing
        events = observer.observe_chunk([0.0, 0.1, 1.5], monotonic=2.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], EVENT_GAP)
        self.assertEqual(events[0]["schema"], QUALITY_JOURNAL_SCHEMA)
        self.assertAlmostEqual(events[0]["details"]["gap_seconds"], 1.4)
        self.assertEqual(observer.gap_count, 1)

    def test_an_irregular_stream_cannot_have_a_gap(self) -> None:
        """Markers are event-driven: a quiet hour is not a defect."""
        observer = _observer(rate_hz=0.0)
        events = observer.observe_chunk([0.0, 3600.0], monotonic=1.0)
        self.assertEqual(events, [])
        self.assertIsNone(observer.expected_period_seconds)

    def test_a_backwards_timestamp_is_reported_as_a_regression(self) -> None:
        observer = _observer()
        events = observer.observe_chunk([1.0, 0.9], monotonic=2.0)
        self.assertEqual([event["event"] for event in events], [EVENT_TIMESTAMP_REGRESSION])
        self.assertEqual(observer.regression_count, 1)

    def test_a_regression_is_not_averaged_into_jitter(self) -> None:
        """A defect is something to report, not a sample interval."""
        clean = _observer()
        clean.observe_chunk([0.0, 0.1, 0.2, 0.3], monotonic=1.0)
        with_regression = _observer()
        with_regression.observe_chunk([0.0, 0.1, 0.2, 0.15, 0.25, 0.35], monotonic=1.0)
        self.assertAlmostEqual(clean.jitter_seconds(), 0.0, places=9)
        self.assertAlmostEqual(with_regression.jitter_seconds(), 0.0, places=9)

    def test_jitter_needs_at_least_two_intervals(self) -> None:
        observer = _observer()
        observer.observe_chunk([1.0, 1.1], monotonic=2.0)
        self.assertIsNone(observer.jitter_seconds())

    def test_non_finite_timestamps_are_skipped_rather_than_poisoning_aggregates(self) -> None:
        observer = _observer()
        observer.observe_chunk([0.0, float("nan"), 0.1, float("inf")], monotonic=1.0)
        self.assertEqual(observer.sample_count, 2)
        self.assertAlmostEqual(observer.last_timestamp, 0.1)

    def test_summary_carries_the_running_aggregates(self) -> None:
        observer = _observer(rate_hz=10.0)
        observer.observe_chunk([0.0, 0.1, 1.5], monotonic=2.0)
        summary = observer.summary(monotonic=9.0)
        self.assertEqual(summary["event"], EVENT_SUMMARY)
        self.assertEqual(summary["details"]["sample_count"], 3)
        self.assertEqual(summary["details"]["gap_count"], 1)
        self.assertEqual(summary["monotonic"], 9.0)


class WallClockJumpDetectorTests(unittest.TestCase):
    def test_the_first_observation_cannot_be_a_jump(self) -> None:
        self.assertIsNone(WallClockJumpDetector().observe(monotonic=1.0, wall=1000.0))

    def test_clocks_advancing_together_is_not_a_jump(self) -> None:
        detector = WallClockJumpDetector()
        detector.observe(monotonic=1.0, wall=1000.0)
        self.assertIsNone(detector.observe(monotonic=2.0, wall=1001.0))

    def test_wall_clock_moving_alone_is_a_quality_event(self) -> None:
        detector = WallClockJumpDetector()
        detector.observe(monotonic=1.0, wall=1000.0)
        event = detector.observe(monotonic=2.0, wall=1060.0)
        self.assertIsNotNone(event)
        self.assertEqual(event["event"], EVENT_CLOCK_JUMP)
        self.assertAlmostEqual(event["details"]["drift_seconds"], 59.0)

    def test_a_backwards_jump_is_detected_too(self) -> None:
        detector = WallClockJumpDetector()
        detector.observe(monotonic=1.0, wall=1000.0)
        event = detector.observe(monotonic=2.0, wall=940.0)
        self.assertIsNotNone(event)
        self.assertLess(event["details"]["drift_seconds"], 0)


class SessionJournalWriterTests(unittest.TestCase):
    def test_records_land_as_one_json_object_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            with SessionJournalWriter(session) as journals:
                journals.append_quality({"event": "gap"})
                journals.append_quality({"event": "summary"})
                journals.append_timing({"observation": "clock_offset"})

            quality_lines = (session / QUALITY_JOURNAL_FILENAME).read_text(encoding="utf-8").splitlines()
            timing_lines = (session / TIMING_JOURNAL_FILENAME).read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line)["event"] for line in quality_lines], ["gap", "summary"])
            self.assertEqual(len(timing_lines), 1)

    def test_an_aborted_session_keeps_what_it_already_wrote(self) -> None:
        """The whole reason 5c exists: no finalization, still evidence."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            journals = SessionJournalWriter(session)
            journals.append_quality({"event": "gap"})
            journals.flush(durable=True)
            # Deliberately never closed -- stands in for a killed process.
            self.assertTrue((session / QUALITY_JOURNAL_FILENAME).is_file())
            self.assertIn("gap", (session / QUALITY_JOURNAL_FILENAME).read_text(encoding="utf-8"))
            journals.close()

    def test_an_unwritable_session_directory_never_breaks_the_recording(self) -> None:
        """Journals are evidence about the recording, not a reason to stop it."""
        journals = SessionJournalWriter(Path("/definitely/not/a/writable/path"))
        journals.append_quality({"event": "gap"})
        journals.flush(durable=True)
        journals.close()

    def test_appending_after_close_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            journals = SessionJournalWriter(session)
            journals.append_quality({"event": "gap"})
            journals.close()
            journals.append_quality({"event": "late"})
            lines = (session / QUALITY_JOURNAL_FILENAME).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)


class SummarizeQualityJournalTests(unittest.TestCase):
    def test_counts_events_and_names_the_streams_involved(self) -> None:
        summary = summarize_quality_journal(
            [
                {"event": EVENT_GAP, "plugin_key": "brainbit", "stream_key": "eeg"},
                {"event": EVENT_GAP, "plugin_key": "brainbit", "stream_key": "eeg"},
                {"event": EVENT_SUMMARY, "plugin_key": "brainbit", "stream_key": "eeg"},
            ]
        )
        self.assertEqual(summary["event_counts"], {EVENT_GAP: 2, EVENT_SUMMARY: 1})
        self.assertEqual(summary["streams"], ["brainbit.eeg"])
        self.assertTrue(summary["has_events"])

    def test_a_journal_of_only_summaries_reports_no_events(self) -> None:
        summary = summarize_quality_journal([{"event": EVENT_SUMMARY, "plugin_key": "x", "stream_key": "y"}])
        self.assertFalse(summary["has_events"])


if __name__ == "__main__":
    unittest.main()
