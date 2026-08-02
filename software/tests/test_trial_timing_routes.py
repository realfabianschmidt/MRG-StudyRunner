from __future__ import annotations

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


class TrialTimingRouteTests(unittest.TestCase):
    def test_prepare_and_duplicate_start_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            payload = {
                "event_id": "start-1",
                "stop_event_id": "stop-1",
                "stimulus_id": "stimulus-1",
                "study_id": "study-a",
                "participant_id": "p01",
                "question_index": 1,
                "question_type": "stimulus",
                "phase": "stimulus_active_start",
                "marker_event": "stimulus_active_start",
                "client_trigger_epoch_ms": 1_760_000_000_000.0,
                "planned_start_epoch_ms": 1_760_000_000_000.0,
                "planned_deadline_epoch_ms": 1_760_000_030_000.0,
            }

            prepared = client.post("/api/trial/prepare", json=payload)
            with patch(
                "study_runner.backend.routes.study.start_trial_session",
                return_value={"marker_value": "start-marker"},
            ) as start_handler:
                first = client.post("/api/start", json=payload)
                duplicate = client.post("/api/start", json=payload)

        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(first.get_json()["duplicate"])
        self.assertTrue(duplicate.get_json()["duplicate"])
        start_handler.assert_called_once()

    def test_event_id_reuse_with_changed_payload_returns_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            payload = {
                "event_id": "marker-1",
                "marker_event": "question_shown",
                "study_id": "study-a",
                "participant_id": "p01",
                "question_index": 1,
                "question_type": "likert",
            }
            with patch(
                "study_runner.backend.routes.study.send_trial_marker",
                return_value={"marker_value": "marker"},
            ):
                first = client.post("/api/marker", json=payload)
                conflict = client.post("/api/marker", json={**payload, "question_index": 2})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)


if __name__ == "__main__":
    unittest.main()
