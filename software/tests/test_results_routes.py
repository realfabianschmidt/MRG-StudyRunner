from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend import create_app


VALIDATED_RESULTS = {
    "participant_id": "p01",
    "study_id": "teststudy",
    "timestamp_start": "2026-07-10T10:00:00Z",
    "timestamp_end": "2026-07-10T10:05:00Z",
    "answers": {},
    "participant_metadata": {},
    "answer_events": [],
    "card_events": [],
}

CONFIG_DATA = {"study_id": "teststudy", "questions": [], "study_settings": {}}


class ResultsRoutesTests(unittest.TestCase):
    def _make_app(self, data_dir: str):
        env = {
            "STUDY_RUNNER_DATA_DIR": data_dir,
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            return create_app()

    def _results_patches(self, save_results_payload):
        return (
            patch("study_runner.backend.routes.load_config", return_value={}),
            patch("study_runner.backend.routes.validate_and_normalize_config", return_value=dict(CONFIG_DATA)),
            patch("study_runner.backend.routes.validate_and_normalize_results", return_value=dict(VALIDATED_RESULTS)),
            patch("study_runner.backend.routes.build_answer_details", return_value=[]),
            patch("study_runner.backend.routes.save_results_payload", save_results_payload),
        )

    def test_save_failure_returns_500_and_preserves_raw_payload(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)

            def broken_save(*args, **kwargs):
                raise OSError("disk full")

            submission = {
                "session_id": "sess-1",
                "study_id": "teststudy",
                "participant_id": "p01",
                "answers": {"q0": 3},
            }
            patches = self._results_patches(broken_save)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                response = app.test_client().post("/api/results", json=submission)

            payload = response.get_json()
            self.assertEqual(response.status_code, 500)
            self.assertFalse(payload["ok"])
            self.assertIn("disk full", payload["error"])

            recovery_file = payload["recovered_file"]
            self.assertIsNotNone(recovery_file)
            recovered = json.loads(Path(recovery_file).read_text(encoding="utf-8"))
            self.assertEqual(recovered, submission)
            self.assertIn("_recovery", recovery_file)

    def test_successful_save_removes_partial_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            client = app.test_client()

            partial = {
                "session_id": "sess-2",
                "study_id": "teststudy",
                "participant_id": "p01",
                "answers": {"q0": 1},
            }
            partial_response = client.post("/api/results/partial", json=partial)
            self.assertEqual(partial_response.status_code, 200)

            candidates = list(Path(data_dir).rglob("sess-2.json"))
            self.assertEqual(len(candidates), 1, f"expected one snapshot, found: {candidates}")
            snapshot_path = candidates[0]

            def working_save(*args, **kwargs):
                return {"json_file": "teststudy/p01/p01.json", "xdf_file": None}

            submission = {**partial, "timestamp_start": "2026-07-10T10:00:00Z", "timestamp_end": "2026-07-10T10:05:00Z"}
            patches = self._results_patches(working_save)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                response = client.post("/api/results", json=submission)

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["ok"])
            self.assertFalse(snapshot_path.exists(), "partial snapshot should be removed after final save")

    def test_partial_snapshot_requires_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)

            response = app.test_client().post(
                "/api/results/partial",
                json={"study_id": "teststudy", "participant_id": "p01"},
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 400)
            self.assertFalse(payload["ok"])
            self.assertIn("session_id", payload["error"])

    def test_partial_snapshot_roundtrip_overwrites_previous(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            client = app.test_client()

            first = {"session_id": "sess-3", "study_id": "teststudy", "answers": {"q0": 1}}
            second = {"session_id": "sess-3", "study_id": "teststudy", "answers": {"q0": 1, "q1": 2}}
            self.assertEqual(client.post("/api/results/partial", json=first).status_code, 200)
            self.assertEqual(client.post("/api/results/partial", json=second).status_code, 200)

            candidates = list(Path(data_dir).rglob("sess-3.json"))
            self.assertEqual(len(candidates), 1)
            stored = json.loads(candidates[0].read_text(encoding="utf-8"))
            self.assertEqual(stored["answers"], {"q0": 1, "q1": 2})
            self.assertIn("server_received_at", stored)


if __name__ == "__main__":
    unittest.main()
