"""Crash-artifact discovery and finalize/discard for recovery_service."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.atomic_io import atomic_write_json
from study_runner.backend.services.recovery_service import (
    RecoveryError,
    discard_recovery_candidate,
    finalize_recovery_candidate,
    list_recovery_candidates,
    recovery_session_sets,
)
from study_runner.plugin_framework.plugin_api import PluginContext


def _context(data_dir: Path) -> PluginContext:
    return PluginContext(
        base_dir=data_dir.parent,
        data_dir=data_dir,
        hardware_config={},
        local_secrets={},
        local_secrets_file=data_dir.parent / "local_secrets.json",
    )


def _config_data() -> dict:
    return {
        "study_id": "study-a",
        "questions": [
            {"type": "participant-id"},
            {"type": "likert", "prompt": "How do you feel?"},
            {"type": "finish"},
        ],
    }


def _optional_config_data() -> dict:
    return {
        "study_id": "study-a",
        "questions": [
            {"type": "participant-id"},
            {"type": "likert", "prompt": "Optional", "required": False},
            {"type": "finish"},
        ],
    }


class RecoveryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "saved_results"
        self.data_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_partial(self, session_id: str, **overrides) -> Path:
        payload = {
            "session_id": session_id,
            "client_id": "tablet-1",
            "study_id": "study-a",
            "participant_id": "hashabc123",
            "client_clock_offset_ms": 0,
            "timestamp_start": "2026-01-01T10:00:00Z",
            "snapshot_at": "2026-01-01T10:05:00Z",
            "current_index": 1,
            "answers": {"q1": "great"},
            "participant_metadata": {},
            "answer_events": [],
            "card_events": [],
        }
        payload.update(overrides)
        path = self.data_dir / "study-a" / "_partial" / f"{session_id}.json"
        atomic_write_json(path, payload)
        return path

    def _write_flush(self, session_id: str, sensor: str = "mr60", **overrides) -> Path:
        payload = {
            "session_id": session_id,
            "study_id": "study-a",
            "participant_id": "hashabc123",
            "sensor": sensor,
            "filename_suffix": f"{sensor}_signals",
            "output_key": f"{sensor}_file",
            "flushed_at": 1_000_100.0,
            "interval_start_epoch": 1_000_000.0,
            "interval_end_epoch": 1_000_100.0,
            "samples": [{"server_received_epoch": 1_000_050.0, "heartRate": 72}],
        }
        payload.update(overrides)
        path = self.data_dir / "study-a" / "_flush" / f"{session_id}_{payload['filename_suffix']}.json"
        atomic_write_json(path, payload)
        return path

    def _write_recovery_dump(self, name: str, **overrides) -> Path:
        payload = {
            "session_id": f"session-{name}",
            "study_id": "study-a",
            "participant_id": "hashdef456",
            "timestamp_start": "2026-01-02T10:00:00Z",
            "timestamp_end": "2026-01-02T10:20:00Z",
            "answers": {"q1": "fine"},
            "participant_metadata": {},
            "answer_events": [],
            "card_events": [],
        }
        payload.update(overrides)
        path = self.data_dir / "study-a" / "_recovery" / f"{name}.json"
        atomic_write_json(path, payload)
        return path

    def test_partial_snapshot_without_saved_result_is_a_candidate(self) -> None:
        self._write_partial("session-1")

        candidates = list_recovery_candidates(self.data_dir)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["kind"], "partial")
        self.assertEqual(candidate["study_id"], "study-a")
        self.assertEqual(candidate["answers_count"], 1)
        self.assertEqual(candidate["last_activity"], "2026-01-01T10:05:00Z")

    def test_partial_snapshot_lists_its_flushed_sensors(self) -> None:
        self._write_partial("session-1")
        self._write_flush("session-1", sensor="mr60")
        self._write_flush("session-1", sensor="brainbit", filename_suffix="brainbit_signals", output_key="brainbit_file")

        [candidate] = list_recovery_candidates(self.data_dir)

        self.assertEqual(sorted(candidate["sensors_flushed"]), ["brainbit", "mr60"])

    def test_session_with_a_saved_result_is_not_a_candidate(self) -> None:
        self._write_partial("session-1")
        participant_dir = self.data_dir / "study-a" / "p01"
        participant_dir.mkdir(parents=True)
        atomic_write_json(
            participant_dir / "p01.json",
            {
                "study_id": "study-a",
                "participant_id": "p01",
                "session_id": "session-1",
                "answers": {"q1": "great"},
                "answer_details": [],
            },
        )

        self.assertEqual(list_recovery_candidates(self.data_dir), [])

    def test_partial_snapshot_is_not_a_candidate_while_its_session_is_still_resumable(self) -> None:
        self._write_partial("session-1")

        candidates = list_recovery_candidates(self.data_dir, active_session_ids={"session-1"})

        self.assertEqual(candidates, [])

    def test_active_finish_session_becomes_recovery_candidate_after_timeout(self) -> None:
        self._write_partial("session-1")

        candidates = list_recovery_candidates(
            self.data_dir,
            active_session_states=[
                {
                    "session_id": "session-1",
                    "status": "active",
                    "current_type": "finish",
                    "last_seen": 100.0,
                }
            ],
            now_epoch=200.0,
            stuck_after_seconds=90.0,
        )

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["stuck_active"])

    def test_recent_finish_session_stays_resumable(self) -> None:
        still_resumable, stuck_active = recovery_session_sets(
            [
                {
                    "session_id": "session-1",
                    "status": "active",
                    "current_type": "finish",
                    "last_seen": 150.0,
                }
            ],
            now_epoch=200.0,
            stuck_after_seconds=90.0,
        )

        self.assertEqual(still_resumable, {"session-1"})
        self.assertEqual(stuck_active, set())

    def test_partial_snapshot_becomes_a_candidate_once_no_longer_active(self) -> None:
        self._write_partial("session-1")

        # A different session is still active; session-1 is not in that set
        # (e.g. the store marked it stale, or it was never re-established).
        candidates = list_recovery_candidates(self.data_dir, active_session_ids={"some-other-session"})

        self.assertEqual(len(candidates), 1)

    def test_recovery_dump_without_saved_result_is_a_candidate(self) -> None:
        self._write_recovery_dump("p01_20260102_100000_abcd1234")

        candidates = list_recovery_candidates(self.data_dir)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["kind"], "recovery_dump")
        self.assertEqual(candidates[0]["answers_count"], 1)

    def test_recovery_dump_is_a_candidate_even_if_its_session_looks_active(self) -> None:
        # A failed *final* save means the participant already finished - the
        # operator needs to see this immediately, not wait for staleness.
        self._write_recovery_dump("p01_dump", session_id="session-dump")

        candidates = list_recovery_candidates(self.data_dir, active_session_ids={"session-dump"})

        self.assertEqual(len(candidates), 1)

    def test_finalize_partial_snapshot_writes_a_browsable_result(self) -> None:
        self._write_partial("session-1")
        result = finalize_recovery_candidate(
            self.data_dir,
            _config_data(),
            {},
            _context(self.data_dir),
            "partial:study-a:session-1",
        )

        json_file = Path(self.temp_dir.name) / result["saved_output"]["json_file"]
        self.assertTrue(json_file.is_file())
        saved = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertTrue(saved["recovered"])
        self.assertEqual(saved["answers"], {"q1": "great"})
        self.assertEqual(
            saved["sensor_summary_provenance"],
            {
                "classification": "legacy_recovery_noncanonical",
                "source": "runtime_memory",
                "canonical": False,
                "canonical_artifact": "card-summary.json",
            },
        )
        self.assertTrue(
            all(
                detail.get("biosignal_interval_source") == "legacy_runtime_memory_noncanonical"
                for detail in saved["answer_details"]
            )
        )
        # participant-id (index 0) + the one likert question (index 1); finish is never an entry.
        self.assertEqual(len(saved["answer_details"]), 2)

    def test_finalize_partial_snapshot_records_skipped_optional_questions(self) -> None:
        self._write_partial(
            "session-optional",
            answers={},
            card_events=[
                {
                    "question_index": 1,
                    "question_type": "likert",
                    "shown_at": "2026-01-01T10:00:10Z",
                }
            ],
        )

        result = finalize_recovery_candidate(
            self.data_dir,
            _optional_config_data(),
            {},
            _context(self.data_dir),
            "partial:study-a:session-optional",
        )

        json_file = Path(self.temp_dir.name) / result["saved_output"]["json_file"]
        saved = json.loads(json_file.read_text(encoding="utf-8"))
        skipped_details = [detail for detail in saved["answer_details"] if detail.get("skipped")]
        self.assertEqual(saved["skipped_questions"], ["q1"])
        self.assertEqual(len(skipped_details), 1)
        self.assertEqual(skipped_details[0]["question_key"], "q1")

    def test_finalize_removes_the_snapshot_from_future_scans(self) -> None:
        self._write_partial("session-1")
        finalize_recovery_candidate(
            self.data_dir, _config_data(), {}, _context(self.data_dir), "partial:study-a:session-1"
        )

        self.assertEqual(list_recovery_candidates(self.data_dir), [])
        self.assertTrue((self.data_dir / "study-a" / "_partial" / "finalized" / "session-1.json").is_file())
        self.assertFalse((self.data_dir / "study-a" / "_partial" / "session-1.json").is_file())

    def test_finalize_splices_in_flushed_sensor_data(self) -> None:
        self._write_partial("session-1")
        self._write_flush("session-1", sensor="mr60")

        result = finalize_recovery_candidate(
            self.data_dir, _config_data(), {}, _context(self.data_dir), "partial:study-a:session-1"
        )

        self.assertIn("mr60_file", result["saved_output"])
        sidecar_file = Path(self.temp_dir.name) / result["saved_output"]["mr60_file"]
        self.assertTrue(sidecar_file.is_file())
        sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
        self.assertEqual(sidecar["sensor"], "mr60")
        self.assertEqual(sidecar["sample_count"], 1)

    def test_finalize_discards_flush_files_after_use(self) -> None:
        self._write_partial("session-1")
        self._write_flush("session-1")

        finalize_recovery_candidate(
            self.data_dir, _config_data(), {}, _context(self.data_dir), "partial:study-a:session-1"
        )

        self.assertEqual(list((self.data_dir / "study-a" / "_flush").glob("*.json")), [])

    def test_finalize_recovery_dump_reuses_its_complete_payload(self) -> None:
        self._write_recovery_dump("p01_dump", session_id="session-dump")

        result = finalize_recovery_candidate(
            self.data_dir, _config_data(), {}, _context(self.data_dir), "recovery_dump:study-a:p01_dump"
        )

        json_file = Path(self.temp_dir.name) / result["saved_output"]["json_file"]
        saved = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertTrue(saved["recovered"])
        self.assertEqual(saved["timestamp_end"], "2026-01-02T10:20:00Z")

    def test_finalize_unknown_recovery_id_raises(self) -> None:
        with self.assertRaises(RecoveryError):
            finalize_recovery_candidate(
                self.data_dir, _config_data(), {}, _context(self.data_dir), "partial:study-a:does-not-exist"
            )

    def test_finalize_rejects_a_path_traversal_attempt(self) -> None:
        with self.assertRaises(RecoveryError):
            finalize_recovery_candidate(
                self.data_dir,
                _config_data(),
                {},
                _context(self.data_dir),
                "partial:..:..%2f..%2fetc",
            )

    def test_discard_moves_snapshot_and_flush_files_without_deleting(self) -> None:
        self._write_partial("session-1")
        self._write_flush("session-1")

        result = discard_recovery_candidate(self.data_dir, "partial:study-a:session-1")

        self.assertTrue(result["ok"])
        self.assertTrue((self.data_dir / "study-a" / "_recovery" / "discarded" / "session-1.json").is_file())
        self.assertFalse((self.data_dir / "study-a" / "_partial" / "session-1.json").is_file())
        self.assertEqual(list((self.data_dir / "study-a" / "_flush").glob("*.json")), [])
        self.assertEqual(list_recovery_candidates(self.data_dir), [])

    def test_discard_unknown_recovery_id_raises(self) -> None:
        with self.assertRaises(RecoveryError):
            discard_recovery_candidate(self.data_dir, "partial:study-a:does-not-exist")

    def test_empty_data_dir_has_no_candidates(self) -> None:
        self.assertEqual(list_recovery_candidates(self.data_dir), [])


if __name__ == "__main__":
    unittest.main()
