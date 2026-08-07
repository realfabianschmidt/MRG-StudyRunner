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


def _load_plain_study(client, study_id: str) -> None:
    """Load a sensorless study so the session start is not blocked by readiness.

    The shipped default study marks its sensor plugins required, so
    /api/study/session/start fail-closes without a built native XDF core -
    including on CI. This test is about the results save marking the session
    completed, not about the recording core.
    """
    response = client.post(
        "/api/config",
        json={
            "study_id": study_id,
            "study_settings": {"sensors_enabled": False, "sensors": {}, "plugins": {}},
            "questions": [{"type": "participant-id"}, {"type": "finish"}],
        },
    )
    assert response.status_code == 200, response.get_json()


class ResultsRoutesTests(unittest.TestCase):
    def _make_app(self, data_dir: str):
        env = {
            "STUDY_RUNNER_DATA_DIR": data_dir,
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            return create_app()

    def _results_patches(self):
        return (
            patch("study_runner.backend.routes.results.load_config", return_value={}),
            patch("study_runner.backend.routes.results.validate_and_normalize_config", return_value=dict(CONFIG_DATA)),
            patch("study_runner.backend.routes.results.validate_and_normalize_results", return_value=dict(VALIDATED_RESULTS)),
            patch("study_runner.backend.routes.results.build_answer_details", return_value=[]),
        )

    def test_save_failure_returns_500_and_preserves_raw_payload(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)

            submission = {
                "session_id": "sess-1",
                "study_id": "teststudy",
                "participant_id": "p01",
                "answers": {"q0": 3},
            }
            patches = self._results_patches()
            with (
                patches[0], patches[1], patches[2], patches[3],
                patch.object(app.config["FINALIZATION_SERVICE"], "commit_submission", side_effect=OSError("disk full")),
            ):
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

            submission = {**partial, "timestamp_start": "2026-07-10T10:00:00Z", "timestamp_end": "2026-07-10T10:05:00Z"}
            patches = self._results_patches()
            with patches[0], patches[1], patches[2], patches[3]:
                response = client.post("/api/results", json=submission)

            self.assertEqual(response.status_code, 202)
            self.assertTrue(response.get_json()["ok"])
            self.assertFalse(snapshot_path.exists(), "partial snapshot should be removed after final save")

    def test_successful_save_marks_study_session_completed(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            client = app.test_client()
            _load_plain_study(client, "teststudy")

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

            submission = {
                "session_id": session_id,
                "study_id": "teststudy",
                "participant_id": "p01",
                "timestamp_start": "2026-07-10T10:00:00Z",
                "timestamp_end": "2026-07-10T10:05:00Z",
            }
            patches = self._results_patches()
            with patches[0], patches[1], patches[2], patches[3]:
                response = client.post("/api/results", json=submission)

            payload = response.get_json()
            self.assertEqual(response.status_code, 202)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["session_completed"])
            self.assertEqual(payload["study_run_state"]["status"], "completed")
            self.assertEqual(app.config["SESSION_STORE"].get(session_id)["status"], "completed")
            self.assertIsNone(app.config["SESSION_STORE"].find_active("teststudy", "p01", "tablet-1"))

    def test_post_commit_bookkeeping_failure_does_not_revoke_accepted_submission(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            patches = self._results_patches()
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patch(
                    "study_runner.backend.routes.results._stop_study_session_tracking",
                    side_effect=OSError("session journal busy"),
                ),
                patch(
                    "study_runner.backend.routes.results._complete_study_run",
                    side_effect=OSError("run journal busy"),
                ),
            ):
                response = app.test_client().post(
                    "/api/results",
                    json={
                        "session_id": "sess-post-commit",
                        "study_id": "teststudy",
                        "participant_id": "p01",
                    },
                )

            payload = response.get_json()
            self.assertEqual(response.status_code, 202)
            self.assertTrue(payload["accepted"])
            self.assertFalse(payload["session_completed"])
            self.assertIsNone(payload["study_run_state"])
            self.assertEqual(len(payload["post_commit_warnings"]), 2)

    def test_successful_save_only_commits_finalization_and_returns_without_network_calls(self) -> None:
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
                patch(
                    "study_runner.plugins.notion_upload.adapter.upload_study_result",
                    side_effect=AssertionError("Notion network call ran inside /api/results"),
                ) as notion_upload,
                patch(
                    "study_runner.plugins.nextcloud_upload.webdav_client.NextcloudPublicShareClient.upload_session_folder",
                    side_effect=AssertionError("Nextcloud network call ran inside /api/results"),
                ) as nextcloud_upload,
            ):
                response = app.test_client().post("/api/results", json=submission)

            payload = response.get_json()
            self.assertEqual(response.status_code, 202)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["finalization_job"]["status"], "queued")
            notion_upload.assert_not_called()
            nextcloud_upload.assert_not_called()
            self.assertEqual(app.config["UPLOAD_JOBS_SERVICE"].counts()["queued"], 0)

            stored_payloads = " ".join(
                path.read_text(encoding="utf-8")
                for path in (Path(data_dir) / "saved_results").rglob("*.json")
            )
            self.assertNotIn("legacy-secret", stored_payloads)
            self.assertNotIn("local-secret", stored_payloads)
            self.assertNotIn("share-secret", stored_payloads)

    def test_canonical_submit_strips_ram_sensor_summaries_before_durable_commit(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)
            captured: dict = {}

            def commit_submission(payload, **_kwargs):
                captured.update(payload)
                return {
                    "job_id": "job-canonical",
                    "session_id": "sess-canonical",
                    "session_path": "teststudy/participants/p01/sessions/canonical",
                    "status": "queued",
                }

            validated = {
                **VALIDATED_RESULTS,
                "biosignal_summary": {"brainbit": {"mean": 999}},
                "card_summary": {"cards": [{"mean": 999}]},
            }
            with (
                patch("study_runner.backend.routes.results.load_config", return_value={}),
                patch(
                    "study_runner.backend.routes.results.validate_and_normalize_config",
                    return_value=dict(CONFIG_DATA),
                ),
                patch(
                    "study_runner.backend.routes.results.validate_and_normalize_results",
                    return_value=validated,
                ),
                patch(
                    "study_runner.backend.routes.results.build_answer_details",
                    return_value=[
                        {
                            "question_index": 0,
                            "biosignal_interval": {"brainbit": {"avg_attention": 999}},
                            "data_warnings": ["RAM-derived warning"],
                        }
                    ],
                ),
                patch.object(
                    app.config["FINALIZATION_SERVICE"],
                    "commit_submission",
                    side_effect=commit_submission,
                ),
            ):
                response = app.test_client().post(
                    "/api/results",
                    json={
                        "session_id": "sess-canonical",
                        "study_id": "teststudy",
                        "participant_id": "p01",
                    },
                )

            self.assertEqual(response.status_code, 202)
            self.assertNotIn("biosignal_summary", captured)
            self.assertNotIn("card_summary", captured)
            self.assertNotIn("biosignal_interval", captured["answer_details"][0])
            self.assertNotIn("data_warnings", captured["answer_details"][0])

    def test_legacy_post_save_scheduling_is_not_called(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = self._make_app(data_dir)

            patches = self._results_patches()
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patch(
                    "study_runner.backend.routes.results._enqueue_upload_jobs",
                    side_effect=OSError("journal disk full"),
                ),
            ):
                response = app.test_client().post(
                    "/api/results",
                    json={"study_id": "teststudy", "participant_id": "p01", "session_id": "sess-no-legacy-upload"},
                )

            payload = response.get_json()
            self.assertEqual(response.status_code, 202)
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
