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
            patch("study_runner.backend.routes.results.load_config", return_value={}),
            patch("study_runner.backend.routes.results.validate_and_normalize_config", return_value=dict(CONFIG_DATA)),
            patch("study_runner.backend.routes.results.validate_and_normalize_results", return_value=dict(VALIDATED_RESULTS)),
            patch("study_runner.backend.routes.results.build_answer_details", return_value=[]),
            patch("study_runner.backend.routes.results.save_results_payload", save_results_payload),
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

    def test_successful_save_marks_study_session_completed(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            client = app.test_client()

            start = client.post(
                "/api/study/session/start",
                json={
                    "study_id": "teststudy",
                    "participant_id": "p01",
                    "client_id": "tablet-1",
                    "current_index": 0,
                    "current_type": "participant-id",
                },
            )
            self.assertEqual(start.status_code, 200)
            session_id = start.get_json()["session"]["session_id"]

            def working_save(*args, **kwargs):
                return {"json_file": "teststudy/p01/p01.json", "xdf_file": None}

            submission = {
                "session_id": session_id,
                "study_id": "teststudy",
                "participant_id": "p01",
                "timestamp_start": "2026-07-10T10:00:00Z",
                "timestamp_end": "2026-07-10T10:05:00Z",
            }
            patches = self._results_patches(working_save)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                response = client.post("/api/results", json=submission)

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["session_completed"])
            self.assertEqual(payload["study_run_state"]["status"], "completed")
            self.assertEqual(app.config["SESSION_STORE"].get(session_id)["status"], "completed")
            self.assertIsNone(app.config["SESSION_STORE"].find_active("teststudy", "p01", "tablet-1"))

    def test_successful_save_only_journals_uploads_and_returns_without_network_calls(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            app.config["HARDWARE_CONFIG"] = {
                "notion": {"enabled": True, "api_key": "legacy-secret"},
                "nextcloud": {"password": "legacy-share-secret"},
            }
            app.config["LOCAL_SECRETS"] = {
                "notion": {"api_key": "local-secret"},
                "nextcloud": {"password": "local-share-secret"},
            }
            config = {
                **CONFIG_DATA,
                "study_settings": {
                    "notion_enabled": True,
                    "nextcloud_enabled": True,
                    "nextcloud_share_link": "https://cloud.example/s/token",
                },
            }

            def working_save(*args, **kwargs):
                return {
                    "participant_dir": "saved_results/teststudy/p01",
                    "json_file": "saved_results/teststudy/p01/p01.json",
                    "xdf_file": None,
                }

            submission = {
                "session_id": "sess-upload",
                "study_id": "teststudy",
                "participant_id": "p01",
            }
            with (
                patch("study_runner.backend.routes.results.load_config", return_value={}),
                patch("study_runner.backend.routes.results.validate_and_normalize_config", return_value=config),
                patch(
                    "study_runner.backend.routes.results.validate_and_normalize_results",
                    return_value={**VALIDATED_RESULTS, "session_id": "sess-upload"},
                ),
                patch("study_runner.backend.routes.results.build_answer_details", return_value=[]),
                patch("study_runner.backend.routes.results.build_biosignal_summary", return_value={"brainbit": {}}),
                patch("study_runner.backend.routes.results.save_results_payload", working_save),
                patch(
                    "study_runner.integrations.notion_upload.adapter.upload_study_result",
                    side_effect=AssertionError("Notion network call ran inside /api/results"),
                ) as notion_upload,
                patch(
                    "study_runner.backend.services.nextcloud_service.NextcloudPublicShareClient.upload_session_folder",
                    side_effect=AssertionError("Nextcloud network call ran inside /api/results"),
                ) as nextcloud_upload,
            ):
                response = app.test_client().post("/api/results", json=submission)

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["ok"])
            self.assertNotIn("upload_jobs", payload)
            self.assertNotIn("upload_job_errors", payload)
            notion_upload.assert_not_called()
            nextcloud_upload.assert_not_called()
            self.assertEqual(app.config["UPLOAD_JOBS_SERVICE"].counts()["queued"], 2)

            stored_payloads = " ".join(
                path.read_text(encoding="utf-8")
                for path in (Path(data_dir) / "saved_results" / "upload_jobs").glob("*.json")
            )
            self.assertNotIn("legacy-secret", stored_payloads)
            self.assertNotIn("local-secret", stored_payloads)
            self.assertNotIn("share-secret", stored_payloads)

    def test_post_save_scheduling_failure_does_not_change_success_response(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)

            def working_save(*args, **kwargs):
                return {"json_file": "teststudy/p01/p01.json", "xdf_file": None}

            patches = self._results_patches(working_save)
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patch(
                    "study_runner.backend.routes.results._enqueue_upload_jobs",
                    side_effect=OSError("journal disk full"),
                ),
            ):
                response = app.test_client().post("/api/results", json={"study_id": "teststudy"})

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["ok"])
            self.assertNotIn("upload_jobs", payload)
            self.assertNotIn("upload_job_errors", payload)

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

    def test_partial_snapshot_roundtrip_merges_without_losing_previous_answers(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            client = app.test_client()

            first = {
                "session_id": "sess-3",
                "study_id": "teststudy",
                "current_index": 2,
                "answers": {"q0": 1},
                "participant_metadata": {"city": "Leipzig"},
                "answer_events": [{"question_index": 0, "answered_at": "2026-07-10T10:01:00Z"}],
                "card_events": [{"question_index": 0, "shown_at": "2026-07-10T10:00:30Z"}],
            }
            second = {
                "session_id": "sess-3",
                "study_id": "teststudy",
                "current_index": 0,
                "answers": {"q0": None, "q1": 2},
                "participant_metadata": {"city": ""},
                "answer_events": [],
                "card_events": [{"question_index": 1, "shown_at": "2026-07-10T10:02:00Z"}],
            }
            self.assertEqual(client.post("/api/results/partial", json=first).status_code, 200)
            self.assertEqual(client.post("/api/results/partial", json=second).status_code, 200)

            candidates = list(Path(data_dir).rglob("sess-3.json"))
            self.assertEqual(len(candidates), 1)
            stored = json.loads(candidates[0].read_text(encoding="utf-8"))
            self.assertEqual(stored["answers"], {"q0": 1, "q1": 2})
            self.assertEqual(stored["participant_metadata"], {"city": "Leipzig"})
            self.assertEqual(stored["current_index"], 2)
            self.assertEqual(len(stored["answer_events"]), 1)
            self.assertEqual(len(stored["card_events"]), 2)
            self.assertIn("server_received_at", stored)


if __name__ == "__main__":
    unittest.main()
