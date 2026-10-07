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
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.apps.server import create_app
from study_runner.apps.server.routes.study import _reconcile_reload_interruption


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
                # This test exercises sensor-runtime recovery. The native XDF
                # gate is mocked at the start boundary below.
                "plugins": {
                    "brainbit": {"enabled": True, "required": False, "settings": {}},
                },
            },
            "questions": [
                {"type": "participant-id"},
                {"type": "likert", "prompt": "How do you feel?"},
                {"type": "finish"},
            ],
        },
    )
    assert response.status_code == 200, response.get_json()


def _load_plain_study(client, study_id: str = "study-a") -> None:
    """Load a study with no sensors, so the test exercises the session lifecycle only.

    The shipped default study marks its sensor plugins required, so
    /api/study/session/start fail-closes on any machine without a built native
    XDF core - including CI, which runs pytest without building one. Without
    this the tests below assert 200 and get 409 for a reason that has nothing
    to do with what they claim to prove.
    """
    response = client.post(
        "/api/config",
        json={
            "study_id": study_id,
            "study_settings": {"sensors_enabled": False, "sensors": {}, "plugins": {}},
            "questions": [
                {"type": "participant-id"},
                {"type": "likert", "prompt": "How do you feel?"},
                {"type": "finish"},
            ],
        },
    )
    assert response.status_code == 200, response.get_json()


def _ack_initial_checkpoint(client, session: dict) -> None:
    response = client.post("/api/results/partial", json={
        "checkpoint_version": 2,
        "checkpoint_sequence": 1,
        "session_id": session["session_id"],
        "study_id": session["study_id"],
        "study_revision": session["study_revision"],
        "participant_id": session["participant_id"],
        "client_id": session["client_id"],
        "current_index": 0,
        "completed_indices": [],
        "answers": {},
        "card_states": {},
        "touched_fields": {},
        "question_metrics": {},
        "participant_metadata": {},
    })
    assert response.status_code == 200, response.get_json()


class StudySessionRouteTests(unittest.TestCase):
    def test_lost_pagehide_is_inferred_from_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            session = {
                "session_id": "session-1", "study_id": "study-a",
                "participant_id": "p01", "client_id": "tablet-1",
            }
            checkpoint = {"active_stimulus": {
                "index": 1, "stimulus_id": "stimulus-1",
                "start_event_id": "start-1", "stop_event_id": "stop-1",
            }}
            store = Mock()
            store.get.return_value = {"last_interruption": {
                "interrupted_by_reload": True, "inferred_from_checkpoint": True,
                "stimulus_id": "stimulus-1", "current_index": 1,
            }}
            service = Mock()
            service.snapshot.return_value = {"preparations": {}, "events": {}}
            with app.app_context():
                app.config["SESSION_STORE"] = store
                app.config["TRIAL_EVENT_SERVICE"] = service
                result = _reconcile_reload_interruption(session, checkpoint)
            store.record_client_event.assert_called_once()
            self.assertTrue(result["inferred_from_checkpoint"])
            self.assertEqual(result["outcome"], "visual_only_interrupted")
            store.mark_interruption_reconciled.assert_called_once_with(
                "session-1", "stimulus-1", "visual_only_interrupted",
            )

    def test_started_reload_attempt_stops_once_before_repetition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            session = {
                "session_id": "session-1", "study_id": "study-a",
                "participant_id": "p01", "client_id": "tablet-1",
                "last_interruption": {
                    "interrupted_by_reload": True, "stimulus_id": "stimulus-1",
                    "start_event_id": "start-1", "stop_event_id": "stop-1",
                },
            }
            store = Mock()
            service = Mock()
            service.snapshot.return_value = {
                "preparations": {"stimulus-1": {
                    "session_id": "session-1", "event_id": "start-1",
                    "stop_event_id": "stop-1", "question_index": 1,
                }},
                "events": {"start-1": {"status": "done"}},
            }
            service.execute.return_value = {"server_received_epoch_ms": 1234.0}
            with app.app_context():
                app.config["SESSION_STORE"] = store
                app.config["TRIAL_EVENT_SERVICE"] = service
                result = _reconcile_reload_interruption(session, {"active_stimulus": {
                    "index": 1, "stimulus_id": "stimulus-1",
                }})
            self.assertEqual(result["outcome"], "stop_confirmed")
            self.assertEqual(result["server_stop_received_epoch_ms"], 1234.0)
            service.execute.assert_called_once()
            stop_payload = service.execute.call_args.args[2]
            self.assertEqual(stop_payload["time_source"], "server_receipt")
            self.assertEqual(stop_payload["phase"], "stimulus_interrupted_by_reload")
            store.mark_interruption_reconciled.assert_called_once()

    def test_checkpoint_sequence_rejects_late_and_conflicting_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = _app(temp_dir).test_client()
            _load_plain_study(client)
            started = client.post("/api/study/session/start", json={
                "participant_id": "hash1234", "client_id": "tablet-1",
            })
            self.assertEqual(started.status_code, 200)
            session = started.get_json()["session"]
            checkpoint = {
                "checkpoint_version": 2, "checkpoint_sequence": 2,
                "session_id": session["session_id"], "study_id": session["study_id"],
                "study_revision": session["study_revision"],
                "participant_id": session["participant_id"], "client_id": session["client_id"],
                "current_index": 0, "completed_indices": [], "answers": {},
                "card_states": {}, "touched_fields": {}, "question_metrics": {},
            }
            self.assertEqual(client.post("/api/results/partial", json=checkpoint).status_code, 200)
            duplicate = client.post("/api/results/partial", json=checkpoint)
            self.assertEqual(duplicate.status_code, 200)
            self.assertTrue(duplicate.get_json()["duplicate"])
            stale = client.post("/api/results/partial", json={**checkpoint, "checkpoint_sequence": 1})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.get_json()["code"], "checkpoint_stale")
            conflict = client.post("/api/results/partial", json={**checkpoint, "current_index": 1})
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.get_json()["code"], "checkpoint_conflict")
            legacy = client.post("/api/results/partial", json={
                "session_id": session["session_id"], "study_id": session["study_id"],
            })
            self.assertEqual(legacy.status_code, 409)
            self.assertEqual(legacy.get_json()["code"], "checkpoint_stale")

    def test_resume_requires_bound_study_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = _app(temp_dir).test_client()
            _load_plain_study(client)
            started = client.post("/api/study/session/start", json={
                "participant_id": "hash1234", "client_id": "tablet-1",
            })
            self.assertEqual(started.status_code, 200)
            session = started.get_json()["session"]
            _ack_initial_checkpoint(client, session)
            resumed = client.post("/api/study/session/resume", json={
                "session_id": session["session_id"], "study_id": session["study_id"],
                "participant_id": session["participant_id"], "client_id": session["client_id"],
                "study_revision": "wrong-revision",
            })
            self.assertEqual(resumed.status_code, 409)
            self.assertEqual(resumed.get_json()["code"], "study_revision_conflict")

    def test_session_survives_a_simulated_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_app = _app(temp_dir)
            first_client = first_app.test_client()
            _load_plain_study(first_client)
            start = first_client.post(
                "/api/study/session/start",
                json={"participant_id": "hash1234", "client_id": "tablet-1", "current_index": 0, "current_type": "participant-id"},
            )
            self.assertEqual(start.status_code, 200)
            session_id = start.get_json()["session"]["session_id"]
            session = start.get_json()["session"]
            _ack_initial_checkpoint(first_client, session)

            # A fresh create_app() against the same DATA_DIR is what a process restart does.
            second_app = _app(temp_dir)
            second_client = second_app.test_client()
            resume = second_client.post(
                "/api/study/session/resume",
                json={"session_id": session_id, "study_id": "study-a", "study_revision": session["study_revision"], "participant_id": "hash1234", "client_id": "tablet-1"},
            )

        self.assertEqual(resume.status_code, 200)
        body = resume.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["session"]["session_id"], session_id)
        self.assertEqual(body["session"]["status"], "active")

    def test_resume_after_restart_restarts_sensors_too(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("study_runner.apps.server.initialize_plugins"),
                patch("study_runner.data_core.host.sensor_coordinator_service.initialize_plugin") as initialize_plugin,
                patch("study_runner.data_core.host.sensor_coordinator_service.run_runtime_action", return_value={"ok": True}) as run_action,
                patch("study_runner.data_core.host.recording_runtime.RecordingRuntimeService.start_session",
                      return_value={"recording_expected": True, "status": "running", "plugins": ["brainbit"]}),
                # After a restart the sensor process is gone, so it reports not running.
                patch("study_runner.data_core.host.sensor_coordinator_service.get_plugin_status", return_value={"running": False}),
            ):
                first_app = _app(temp_dir, disable_hardware=False)
                first_client = first_app.test_client()
                _load_sensor_study(first_client)
                start = first_client.post(
                    "/api/study/session/start",
                    json={
                        "study_id": "study-a",
                        "participant_id": "hash1234",
                        "client_id": "tablet-1",
                        "current_index": 0,
                        "current_type": "participant-id",
                    },
                )
                session_id = start.get_json()["session"]["session_id"]
                session = start.get_json()["session"]
                _ack_initial_checkpoint(first_client, session)

                second_app = _app(temp_dir, disable_hardware=False)
                self.assertIsNone(second_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
                initialize_plugin.reset_mock()
                run_action.reset_mock()
                second_client = second_app.test_client()
                resume = second_client.post(
                    "/api/study/session/resume",
                    json={"session_id": session_id, "study_id": "study-a", "study_revision": session["study_revision"], "participant_id": "hash1234", "client_id": "tablet-1"},
                )

        # Sensors were dark after the "restart"; resuming must bring them back
        # up, or a recovered tablet would silently record nothing new.
        self.assertEqual(resume.status_code, 200)
        self.assertIsNotNone(second_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"))
        self.assertGreater(initialize_plugin.call_count, 0)
        self.assertGreater(run_action.call_count, 0)

    def test_start_retry_keeps_the_original_sensor_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "study_runner.data_core.host.recording_runtime.RecordingRuntimeService.start_session",
                return_value={"recording_expected": True, "status": "running", "plugins": ["brainbit"]},
            ) as start_recording:
                app = _app(temp_dir)
                client = app.test_client()
                _load_sensor_study(client)
                payload = {
                    "study_id": "study-a", "participant_id": "hash1234",
                    "client_id": "tablet-1", "current_index": 0,
                    "current_type": "participant-id",
                }
                first = client.post("/api/study/session/start", json=payload)
                self.assertEqual(first.status_code, 200, first.get_json())
                app.config["SESSION_SENSOR_OVERRIDES"] = {"brainbit": False}
                retry = client.post("/api/study/session/start", json=payload)
                self.assertEqual(retry.status_code, 200, retry.get_json())
                self.assertEqual(first.get_json()["session"]["session_id"], retry.get_json()["session"]["session_id"])
                self.assertTrue(start_recording.call_args.kwargs["selected_sensors"]["brainbit"])

    def test_session_start_does_not_touch_hardware_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            with (
                patch("study_runner.apps.server.routes.helpers.initialize_plugin") as initialize_plugin,
                patch("study_runner.apps.server.routes.helpers.run_runtime_action", return_value={"ok": True}) as run_action,
            ):
                client = app.test_client()
                _load_plain_study(client)
                response = client.post(
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
            _load_plain_study(client)
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
            _load_plain_study(client)
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
