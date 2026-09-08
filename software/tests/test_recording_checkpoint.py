"""Package 5h: how far a recording is *confirmed* to be on disk.

These tests care mostly about the crash cases, because the confirmed
prefix only earns its keep when a process died: a torn last line, a
generation that never reached its first flush, a segment that closed
cleanly and must therefore produce no warning at all.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.quality_journal import (
    EVENT_UNCONFIRMED_TAIL,
    QUALITY_JOURNAL_FILENAME,
)
from study_runner.contracts.recording_checkpoint import (
    CHECKPOINT_JOURNAL_FILENAME,
    checkpoint_record,
    confirmed_stream_positions,
    last_checkpoint_for_generation,
    read_checkpoints,
    stream_commit,
)
from study_runner.data_core.host.recording_runtime_support import report_unconfirmed_tail
from study_runner.data_core.worker.session_journals import SessionJournalWriter


def _checkpoint(generation: int, monotonic: float, *, reason: str = "periodic", count: int = 100):
    return checkpoint_record(
        generation=generation,
        monotonic=monotonic,
        segment_relative_path=f"raw/plugins/fixture/g{generation}.xdf",
        reason=reason,
        streams=[
            stream_commit(
                plugin_key="fixture",
                stream_key="values",
                sample_count=count,
                last_timestamp=1000.0 + count,
            )
        ],
    )


def _unwritable_dir(temp_dir: Path) -> Path:
    """A directory path that cannot be created, on every platform.

    A made-up absolute path is not enough: on Windows ``/no/such/path``
    resolves under the current drive root and is often creatable, which
    turns a durability test into one that quietly asserts nothing. A child
    of a regular *file* can never be a directory anywhere.
    """
    blocker = temp_dir / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    return blocker / "session"


def _paths(root: Path) -> SimpleNamespace:
    """A stand-in for ArtifactPaths: the reporter only reads ``root``."""
    return SimpleNamespace(root=root)


class CheckpointJournalTests(unittest.TestCase):
    def test_a_torn_final_line_does_not_discard_the_confirmed_prefix(self) -> None:
        """The expected state after a hard crash, not an error."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / CHECKPOINT_JOURNAL_FILENAME
            good = json.dumps(_checkpoint(1, 5.0))
            path.write_text(good + "\n" + good[: len(good) // 2], encoding="utf-8")
            records = read_checkpoints(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["monotonic"], 5.0)

    def test_a_missing_journal_reads_as_nothing_confirmed(self) -> None:
        self.assertEqual(read_checkpoints(Path("/nowhere/checkpoints.jsonl")), [])

    def test_the_newest_checkpoint_of_the_asked_generation_wins(self) -> None:
        records = [_checkpoint(1, 5.0), _checkpoint(2, 99.0), _checkpoint(1, 20.0)]
        latest = last_checkpoint_for_generation(records, generation=1)
        self.assertEqual(latest["monotonic"], 20.0)

    def test_a_generation_with_no_checkpoint_returns_none(self) -> None:
        self.assertIsNone(last_checkpoint_for_generation([_checkpoint(1, 5.0)], generation=3))

    def test_positions_are_keyed_by_plugin_and_stream(self) -> None:
        positions = confirmed_stream_positions(_checkpoint(1, 5.0, count=42))
        self.assertEqual(positions["fixture.values"]["sample_count"], 42)


class AppendCheckpointTests(unittest.TestCase):
    def test_a_checkpoint_is_durable_the_moment_it_is_appended(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            journals = SessionJournalWriter(session)
            self.assertTrue(journals.append_checkpoint(_checkpoint(1, 5.0)))
            # Read back without closing: the fsync already happened.
            records = read_checkpoints(session / CHECKPOINT_JOURNAL_FILENAME)
            self.assertEqual(len(records), 1)
            journals.close()

    def test_an_unwritable_session_reports_the_checkpoint_as_unconfirmed(self) -> None:
        """A checkpoint is a claim about durability; failing it must say so."""
        with tempfile.TemporaryDirectory() as temp_dir:
            journals = SessionJournalWriter(_unwritable_dir(Path(temp_dir)))
            self.assertFalse(journals.append_checkpoint(_checkpoint(1, 5.0)))
            journals.close()


class UnconfirmedTailTests(unittest.TestCase):
    def test_a_crashed_generation_leaves_its_boundary_in_the_quality_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            with SessionJournalWriter(session) as journals:
                journals.append_checkpoint(_checkpoint(1, 5.0, count=250))
            reported = report_unconfirmed_tail(_paths(session), generation=1, monotonic=9.0)
            self.assertEqual(reported, 1)
            lines = (session / QUALITY_JOURNAL_FILENAME).read_text(encoding="utf-8").splitlines()
            event = json.loads(lines[-1])
            self.assertEqual(event["event"], EVENT_UNCONFIRMED_TAIL)
            self.assertEqual(event["stream_key"], "values")
            self.assertEqual(event["details"]["confirmed_sample_count"], 250)
            self.assertEqual(event["details"]["generation"], 1)

    def test_a_cleanly_frozen_generation_produces_no_warning(self) -> None:
        """Otherwise operators learn to ignore a warning that means nothing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            with SessionJournalWriter(session) as journals:
                journals.append_checkpoint(_checkpoint(1, 5.0))
                journals.append_checkpoint(_checkpoint(1, 8.0, reason="freeze"))
            self.assertEqual(report_unconfirmed_tail(_paths(session), generation=1, monotonic=9.0), 0)
            self.assertFalse((session / QUALITY_JOURNAL_FILENAME).exists())

    def test_a_generation_that_died_before_its_first_flush_still_says_so(self) -> None:
        """"Nothing confirmed" is the strongest warning, not the absence of one."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            self.assertEqual(report_unconfirmed_tail(_paths(session), generation=1, monotonic=9.0), 1)
            event = json.loads((session / QUALITY_JOURNAL_FILENAME).read_text(encoding="utf-8").strip())
            self.assertEqual(event["event"], EVENT_UNCONFIRMED_TAIL)
            self.assertEqual(event["details"]["confirmed_sample_count"], 0)
            self.assertIn("no checkpoint survived", event["details"]["note"])

    def test_a_first_generation_start_is_not_a_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            self.assertEqual(report_unconfirmed_tail(_paths(session), generation=0, monotonic=1.0), 0)
            self.assertFalse((session / QUALITY_JOURNAL_FILENAME).exists())

    def test_reporting_never_raises_when_the_session_cannot_be_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            unwritable = _unwritable_dir(Path(temp_dir))
            self.assertEqual(report_unconfirmed_tail(_paths(unwritable), generation=1, monotonic=1.0), 0)


if __name__ == "__main__":
    unittest.main()
