"""Package A1: reducing quality.jsonl to three sentences, not thirty numbers.

Package 5c gave every session a quality journal and 5h taught it to record
unconfirmed tails too, but nothing ever read either back for the UI. These
tests cover the health-level derivation and the "never measured" vs.
"measured and found clean" distinction, since a wrong answer there is a
confident wrong answer about data integrity -- the exact thing this
project's other packages spend their effort avoiding.
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

from study_runner.runtime_core.studies.session_quality_summary import (
    HEALTH_ATTENTION,
    HEALTH_CLEAN,
    HEALTH_UNKNOWN,
    HEALTH_WARNINGS,
    summarize_session_quality,
)


def _write_journal(session_root: Path, records: list[dict]) -> None:
    session_root.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(record) for record in records) + "\n"
    (session_root / "quality.jsonl").write_text(lines, encoding="utf-8")


class MissingOrEmptyJournalTests(unittest.TestCase):
    def test_a_session_with_no_journal_is_unknown_not_clean(self) -> None:
        """5c did not exist yet for this session; that is not the same as clean."""
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = summarize_session_quality(Path(temp_dir))
            self.assertEqual(summary["recording_health"], HEALTH_UNKNOWN)
            self.assertEqual(summary["findings"], [])
            self.assertIsNone(summary["kept_up"])

    def test_an_empty_journal_file_is_clean_not_unknown(self) -> None:
        """The file exists and was read; there is simply nothing in it yet."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            (session_root / "quality.jsonl").write_text("", encoding="utf-8")
            summary = summarize_session_quality(session_root)
            self.assertEqual(summary["recording_health"], HEALTH_CLEAN)

    def test_a_torn_final_line_does_not_discard_the_lines_before_it(self) -> None:
        """The expected shape of a crash (5h), not a reason to give up."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            good = json.dumps({"event": "gap", "stream_key": "eeg", "details": {}})
            (session_root / "quality.jsonl").write_text(good + "\n" + good[:10], encoding="utf-8")
            summary = summarize_session_quality(session_root)
            self.assertEqual(summary["findings"], [{"kind": "gap", "stream_key": "eeg", "count": 1}])


class HealthLevelTests(unittest.TestCase):
    def test_only_summary_events_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [{"event": "summary", "stream_key": "eeg", "details": {"sample_count": 100}}],
            )
            summary = summarize_session_quality(session_root)
            self.assertEqual(summary["recording_health"], HEALTH_CLEAN)

    def test_gaps_and_clock_jumps_are_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [{"event": "clock_jump", "details": {"drift_seconds": 59.2}}],
            )
            summary = summarize_session_quality(session_root)
            self.assertEqual(summary["recording_health"], HEALTH_WARNINGS)
            self.assertEqual(summary["findings"][0]["max_drift_seconds"], 59.2)

    def test_a_timestamp_regression_needs_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [{"event": "timestamp_regression", "stream_key": "eeg", "details": {}}],
            )
            summary = summarize_session_quality(session_root)
            self.assertEqual(summary["recording_health"], HEALTH_ATTENTION)

    def test_an_unconfirmed_tail_needs_attention_even_alongside_a_mere_gap(self) -> None:
        """The worse finding decides the overall health, not the first one."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [
                    {"event": "gap", "stream_key": "eeg", "details": {}},
                    {
                        "event": "unconfirmed_tail",
                        "stream_key": "eeg",
                        "details": {"confirmed_sample_count": 900, "generation": 2},
                    },
                ],
            )
            summary = summarize_session_quality(session_root)
            self.assertEqual(summary["recording_health"], HEALTH_ATTENTION)
            kinds = {finding["kind"] for finding in summary["findings"]}
            self.assertEqual(kinds, {"gap", "unconfirmed_tail"})


class FindingsAreCountedNotListedTests(unittest.TestCase):
    def test_repeated_gaps_on_one_stream_collapse_into_one_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [{"event": "gap", "stream_key": "eeg", "details": {}} for _ in range(3)],
            )
            summary = summarize_session_quality(session_root)
            self.assertEqual(summary["findings"], [{"kind": "gap", "stream_key": "eeg", "count": 3}])

    def test_gaps_on_different_streams_are_separate_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [
                    {"event": "gap", "stream_key": "eeg", "details": {}},
                    {"event": "gap", "stream_key": "ecg", "details": {}},
                ],
            )
            summary = summarize_session_quality(session_root)
            self.assertEqual(
                summary["findings"],
                [
                    {"kind": "gap", "stream_key": "ecg", "count": 1},
                    {"kind": "gap", "stream_key": "eeg", "count": 1},
                ],
            )

    def test_only_the_start_of_an_ingest_backlog_is_counted_not_the_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [
                    {
                        "event": "ingest_backlog",
                        "stream_key": "eeg",
                        "details": {"cleared": False, "peak_fill_ratio": 0.4},
                    },
                    {
                        "event": "ingest_backlog",
                        "stream_key": "eeg",
                        "details": {"cleared": True, "peak_fill_ratio": 0.4},
                    },
                ],
            )
            summary = summarize_session_quality(session_root)
            self.assertEqual(
                summary["findings"],
                [{"kind": "ingest_backlog", "stream_key": "eeg", "count": 1, "peak_fill_ratio": 0.4}],
            )


class KeptUpTests(unittest.TestCase):
    def test_no_summary_evidence_means_unknown_not_a_default_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(session_root, [{"event": "gap", "stream_key": "eeg", "details": {}}])
            summary = summarize_session_quality(session_root)
            self.assertIsNone(summary["kept_up"])

    def test_a_low_peak_fill_ratio_with_no_backlog_finding_is_kept_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [{"event": "summary", "stream_key": "eeg", "details": {"peak_fill_ratio": 0.02}}],
            )
            summary = summarize_session_quality(session_root)
            self.assertTrue(summary["kept_up"])

    def test_kept_up_and_the_backlog_finding_never_disagree(self) -> None:
        """Both are derived from the same finding, not two separate thresholds."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session_root = Path(temp_dir)
            _write_journal(
                session_root,
                [
                    {
                        "event": "ingest_backlog",
                        "stream_key": "eeg",
                        "details": {"cleared": False, "peak_fill_ratio": 0.9},
                    },
                    {"event": "summary", "stream_key": "eeg", "details": {"peak_fill_ratio": 0.9}},
                ],
            )
            summary = summarize_session_quality(session_root)
            self.assertFalse(summary["kept_up"])
            self.assertTrue(any(f["kind"] == "ingest_backlog" for f in summary["findings"]))


if __name__ == "__main__":
    unittest.main()
