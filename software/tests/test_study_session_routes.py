"""End-to-end proof of T7's core promise: a tablet session survives a server restart.

Before session_store.py, STUDY_SESSIONS lived only in current_app.config, so
resume always 404'd after a restart. These tests rebuild the Flask app
against the same DATA_DIR (the same thing a real process restart does) and
assert the tablet's resume call still succeeds.
"""
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


def _app(data_dir: str, *, disable_hardware: bool = True):
    env = {
        "STUDY_RUNNER_DATA_DIR": data_dir,
        "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
    }
    if disable_hardware:
        env["STUDY_RUNNER_DISABLE_HARDWARE"] = "1"
    else:
        env["STUDY_RUNNER_DISABLE_HARDWARE"] = "0"
    with patch.dict(
        os.environ,
        env,
        clear=False,
    ):
        return create_app()


def _load_sensor_study(client) -> None:
    response = client.post(
        "/api/config",
        json={
            "study_id": "study-a",
            "study_settings": {
                "sensors_enabled": True,
                "sensors": {"brainbit": True, "mini_radar": False, "camera_emotion": False},
            },
            "questions": [
                {"type": "participant-id"},
                {"type": "likert", "prompt": "How do you feel?"},
                {"type": "finish"},
            ],
        },
    )
    assert response.status_code == 200, response.get_json()


class StudySessionRouteTests(unittest.TestCase):
    def test_session_survives_a_simulated_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_app = _app(temp_dir)
            first_client = first_app.test_client()
            start = first_client.post(
                "/api/study/session/start",
                json={"participant_id": "hash1234", "current_index": 0, "current_type": "participant-id"},
            )
            self.assertEqual(start.status_code, 200)
            session_id = start.get_json()["session"]["session_id"]

            # A fresh create_app() against the same DATA_DIR is what a process restart does.
            second_app = _app(temp_dir)
            second_client = second_app.test_client()
            resume = second_client.post(
                "/api/study/session/resume",
                json={"session_id": session_id, "participant_id": "hash1234"},
            )

        self.assertEqual(resume.status_code, 200)
        body = resume.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["session"]["session_id"], session_id)
        self.assertEqual(body["session"]["status"], "active")

    def test_resume_after_restart_restarts_sensors_too(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("study_runner.backend.initialize_plugins"),
                patch("study_runner.backend.services.sensor_coordinator_service.initialize_plugin") as initialize_plugin,
                patch("study_runner.backend.services.sensor_coordinator_service.run_runtime_action", return_value={"ok": True}) as run_action,
            ):
                first_app = _app(temp_dir, disable_hardware=False)
                first_client = first_app.test_client()
                _load_sensor_study(first_client)
                start = first_client.post(
                    "/api/study/session/start",
                    json={
                        "study_id": "study-a",
                        "participant_id": "hash1234",
                        "current_index": 0,
                        "current_type": "participant-id",
                    },
                )
                session_id = start.get_json()["session"]["session_id"]

                second_app = _app(temp_dir, disable_hardware=False)
                self.assertIsNone(second_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
                initialize_plugin.reset_mock()
                run_action.reset_mock()
                second_client = second_app.test_client()
                resume = second_client.post(
                    "/api/study/session/resume",
                    json={"session_id": session_id, "study_id": "study-a", "participant_id": "hash1234"},
                )

        # Sensors were dark after the "restart"; resuming must bring them back
        # up, or a recovered tablet would silently record nothing new.
        self.assertEqual(resume.status_code, 200)
        self.assertIsNotNone(second_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
        self.assertGreater(initialize_plugin.call_count, 0)
        self.assertGreater(run_action.call_count, 0)

    def test_session_start_does_not_touch_hardware_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            with (
                patch("study_runner.backend.routes.helpers.initialize_plugin") as initialize_plugin,
                patch("study_runner.backend.routes.helpers.run_runtime_action", return_value={"ok": True}) as run_action,
            ):
                response = app.test_client().post(
                    "/api/study/session/start",
                    json={"participant_id": "hash1234", "current_index": 0, "current_type": "participant-id"},
                )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["active_plugins"], [])
        self.assertEqual(app.config["ACTIVE_STUDY_SENSOR_PLUGINS"], [])
        self.assertFalse(app.config["ACTIVE_STUDY_HARDWARE_CONFIG"]["brainbit"]["enabled"])
        self.assertFalse(app.config["ACTIVE_STUDY_HARDWARE_CONFIG"]["mini_radar"]["enabled"])
        initialize_plugin.assert_not_called()
        run_action.assert_not_called()

    def test_resume_rejects_matching_session_id_with_wrong_participant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            start = client.post(
                "/api/study/session/start",
                json={"participant_id": "hash1234", "current_index": 0, "current_type": "participant-id"},
            )
            session_id = start.get_json()["session"]["session_id"]

            resume = client.post(
                "/api/study/session/resume",
                json={"session_id": session_id, "participant_id": "other1234"},
            )

        self.assertEqual(resume.status_code, 404)

    def test_resume_without_a_known_session_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            response = app.test_client().post(
                "/api/study/session/resume",
                json={"session_id": "no-such-session", "participant_id": "hash1234"},
            )

        self.assertEqual(response.status_code, 404)

    def test_stopped_session_cannot_be_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            start = client.post(
                "/api/study/session/start",
                json={"participant_id": "hash1234", "current_index": 0, "current_type": "participant-id"},
            )
            session_id = start.get_json()["session"]["session_id"]

            stop = client.post("/api/study/session/stop", json={"session_id": session_id})
            resume = client.post(
                "/api/study/session/resume",
                json={"session_id": session_id, "participant_id": "hash1234"},
            )

        self.assertEqual(stop.status_code, 200)
        self.assertEqual(resume.status_code, 404)


if __name__ == "__main__":
    unittest.main()
