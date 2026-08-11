from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


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
            now_ms = time.time() * 1000.0
            payload = {
                "event_id": "start-1",
                "stop_event_id": "stop-1",
                "stimulus_id": "stimulus-1",
                "study_id": "study-a",
                "participant_id": "p01",
                "session_id": "session-recording",
                "client_id": "client-a",
                "question_index": 1,
                "question_type": "stimulus",
                "phase": "stimulus_active_start",
                "marker_event": "stimulus_active_start",
                "client_trigger_epoch_ms": now_ms + 1_000.0,
                "planned_start_epoch_ms": now_ms + 1_000.0,
                "planned_deadline_epoch_ms": now_ms + 31_000.0,
            }

            prepared = client.post("/api/trial/prepare", json=payload)
            with (
                patch(
                    "study_runner.backend.routes.study.start_trial_session",
                    return_value={"marker_value": "start-marker"},
                ) as start_handler,
                patch(
                    "study_runner.backend.routes.study._require_trial_start_runtime",
                    return_value={"active_session": True, "session_id": "session-test"},
                ),
            ):
                first = client.post("/api/start", json=payload)
                duplicate = client.post("/api/start", json=payload)

        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(first.get_json()["duplicate"])
        self.assertTrue(duplicate.get_json()["duplicate"])
        start_handler.assert_called_once()
        self.assertEqual(prepared.get_json()["deadline"]["status"], "armed")

    def test_start_without_durable_prepare_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            now_ms = time.time() * 1000.0
            payload = {
                "event_id": "start-unprepared",
                "stop_event_id": "stop-unprepared",
                "stimulus_id": "stimulus-unprepared",
                "study_id": "study-a",
                "participant_id": "p01",
                "question_index": 1,
                "question_type": "stimulus",
                "planned_start_epoch_ms": now_ms + 1_000.0,
                "planned_deadline_epoch_ms": now_ms + 31_000.0,
            }
            with patch("study_runner.backend.routes.study.start_trial_session") as handler:
                response = client.post("/api/start", json=payload)

        self.assertEqual(response.status_code, 428)
        self.assertEqual(response.get_json()["code"], "trial_prepare_required")
        handler.assert_not_called()

    def test_local_admin_override_is_persisted_and_scoped_to_prepare_only(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            body = {
                "event_id": "override-start",
                "stimulus_id": "override-stimulus",
                "reason": "Operator verified the local routing failure.",
            }

            remote = client.post(
                "/api/admin/trials/prepare-overrides",
                json=body,
                environ_overrides={"REMOTE_ADDR": "192.0.2.10"},
            )
            created = client.post("/api/admin/trials/prepare-overrides", json=body)
            fetched = client.get("/api/admin/trials/prepare-overrides/override-start")

            with patch.dict(os.environ, env, clear=False):
                restarted = create_app()
            persisted = restarted.test_client().get(
                "/api/admin/trials/prepare-overrides/override-start"
            )

        self.assertEqual(remote.status_code, 403)
        self.assertEqual(created.status_code, 200)
        self.assertFalse(created.get_json()["bypasses_recording_readiness"])
        self.assertEqual(created.get_json()["override"]["scope"], "trial_prepare_only")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(persisted.status_code, 200)

    def test_local_override_allows_only_matching_event_and_still_arms_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            client.post(
                "/api/admin/trials/prepare-overrides",
                json={
                    "event_id": "override-start",
                    "stimulus_id": "override-stimulus",
                    "reason": "Prepare request was lost on the lab LAN.",
                },
            )
            now_ms = time.time() * 1000.0
            payload = {
                "event_id": "override-start",
                "stop_event_id": "override-stop",
                "stimulus_id": "override-stimulus",
                "study_id": "study-a",
                "participant_id": "p01",
                "question_index": 1,
                "question_type": "stimulus",
                "planned_start_epoch_ms": now_ms + 1_000.0,
                "planned_deadline_epoch_ms": now_ms + 31_000.0,
            }
            prepared = client.post("/api/trial/prepare", json=payload)
            with (
                patch(
                    "study_runner.backend.routes.study.start_trial_session",
                    return_value={"marker_value": "start-marker"},
                ) as handler,
                patch(
                    "study_runner.backend.routes.study._require_trial_start_runtime",
                    return_value={"active_session": True, "session_id": "session-test"},
                ),
            ):
                response = client.post("/api/start", json=payload)

        self.assertEqual(prepared.status_code, 200)
        self.assertTrue(prepared.get_json()["overridden"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["prepare_gate"]["source"], "local_admin_override")
        self.assertEqual(response.get_json()["deadline"]["status"], "armed")
        handler.assert_called_once()

    def test_override_does_not_bypass_matching_active_session_gate(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            now_ms = time.time() * 1000.0
            payload = {
                "event_id": "override-gated",
                "stop_event_id": "override-gated-stop",
                "stimulus_id": "override-gated-stimulus",
                "study_id": "study-a",
                "participant_id": "p01",
                "session_id": "missing-session",
                "client_id": "client-a",
                "question_index": 1,
                "question_type": "stimulus",
                "planned_start_epoch_ms": now_ms + 1_000.0,
                "planned_deadline_epoch_ms": now_ms + 31_000.0,
            }
            client.post(
                "/api/admin/trials/prepare-overrides",
                json={
                    "event_id": payload["event_id"],
                    "stimulus_id": payload["stimulus_id"],
                    "reason": "Local operator accepted a prepare transport failure.",
                },
            )
            self.assertEqual(client.post("/api/trial/prepare", json=payload).status_code, 200)
            config = {"study_id": "study-a", "study_settings": {"plugins": {}}}
            with (
                patch("study_runner.backend.routes.study._current_config_data", return_value=config),
                patch("study_runner.backend.routes.study.start_trial_session") as handler,
            ):
                response = client.post("/api/start", json=payload)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "trial_runtime_not_ready")
        self.assertFalse(response.get_json()["runtime_gate"]["active_session"])
        handler.assert_not_called()

    def test_required_recording_must_be_healthy_but_completed_retry_remains_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            now_ms = time.time() * 1000.0
            payload = {
                "event_id": "recording-gated-start",
                "stop_event_id": "recording-gated-stop",
                "stimulus_id": "recording-gated-stimulus",
                "study_id": "study-a",
                "participant_id": "p01",
                "session_id": "session-recording",
                "client_id": "client-a",
                "question_index": 1,
                "question_type": "stimulus",
                "planned_start_epoch_ms": now_ms + 1_000.0,
                "planned_deadline_epoch_ms": now_ms + 31_000.0,
            }
            self.assertEqual(client.post("/api/trial/prepare", json=payload).status_code, 200)
            session = app.config["SESSION_STORE"].start_or_reuse(payload)
            app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {}
            runtime = Mock()
            runtime.current_status.return_value = None
            app.config["RECORDING_RUNTIME_SERVICE"] = runtime
            config = {
                "study_id": "study-a",
                "study_settings": {
                    "plugins": {
                        "brainbit": {"enabled": True, "required": True, "settings": {}},
                    }
                },
            }

            with (
                patch("study_runner.backend.routes.study._current_config_data", return_value=config),
                patch(
                    "study_runner.backend.routes.study.start_trial_session",
                    return_value={"marker_value": "started"},
                ) as handler,
            ):
                unhealthy = client.post("/api/start", json=payload)
                runtime.current_status.return_value = {
                    "session_id": session["session_id"],
                    "status": "recording",
                    "worker_health_failures": 0,
                    "last_error": None,
                }
                first = client.post("/api/start", json=payload)
                app.config["SESSION_STORE"].mark_completed(session["session_id"])
                runtime.current_status.return_value = None
                duplicate = client.post("/api/start", json=payload)

        self.assertEqual(unhealthy.status_code, 503)
        self.assertEqual(unhealthy.get_json()["code"], "trial_runtime_not_ready")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.get_json()["duplicate"])
        handler.assert_called_once()

    def test_prepare_cancel_is_append_only_idempotent_and_has_no_trial_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            now_ms = time.time() * 1000.0
            payload = {
                "event_id": "cancel-start",
                "stop_event_id": "cancel-stop",
                "stimulus_id": "cancel-stimulus",
                "study_id": "study-a",
                "participant_id": "p01",
                "question_index": 1,
                "question_type": "stimulus",
                "planned_start_epoch_ms": now_ms + 1_000.0,
                "planned_deadline_epoch_ms": now_ms + 31_000.0,
            }
            self.assertEqual(client.post("/api/trial/prepare", json=payload).status_code, 200)
            cancel_body = {
                "event_id": payload["event_id"],
                "stimulus_id": payload["stimulus_id"],
                "reason": "tablet_skip",
            }
            first = client.post("/api/trial/prepare/cancel", json=cancel_body)
            duplicate = client.post("/api/trial/prepare/cancel", json=cancel_body)
            invalid = client.post(
                "/api/trial/prepare/cancel",
                json={**cancel_body, "event_id": "other", "reason": "silent_skip"},
            )
            snapshot = app.config["TRIAL_EVENT_SERVICE"].snapshot()

            with patch.dict(os.environ, env, clear=False):
                restarted = create_app()
            recovered = restarted.config["TRIAL_EVENT_SERVICE"].snapshot()

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()["deadline_cancelled"])
        self.assertFalse(first.get_json()["duplicate"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.get_json()["deadline_cancelled"])
        self.assertTrue(duplicate.get_json()["duplicate"])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(snapshot["events"], {})
        self.assertEqual(len(snapshot["prepare_cancellations"]), 1)
        self.assertEqual(snapshot["deadlines"]["cancel-stimulus"]["status"], "cancelled")
        self.assertEqual(recovered["deadlines"]["cancel-stimulus"]["status"], "cancelled")

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

    def test_hardware_disabled_also_disables_lazy_trial_process_context(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            observed = {}

            def inspect_context(_options, context, prior):
                observed.update(context.hardware_config)
                return prior

            with patch(
                "study_runner.backend.services.studies.trial_service.run_trial_marker",
                side_effect=inspect_context,
            ):
                response = client.post(
                    "/api/marker",
                    json={"event_id": "marker-disabled", "marker_event": "question_shown"},
                )

        self.assertEqual(response.status_code, 200)
        for key in ("brainbit", "mini_radar", "camera_emotion"):
            self.assertFalse((observed.get(key) or {}).get("enabled"), key)

    def test_start_timestamp_is_captured_before_runtime_refresh_and_journal_handler(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()
            client = app.test_client()
            now_ms = time.time() * 1000.0
            payload = {
                "event_id": "start-ingress",
                "stop_event_id": "stop-ingress",
                "stimulus_id": "stimulus-ingress",
                "study_id": "study-a",
                "participant_id": "p01",
                "question_index": 1,
                "question_type": "stimulus",
                "client_trigger_epoch_ms": now_ms,
                "planned_start_epoch_ms": now_ms + 1_000.0,
                "planned_deadline_epoch_ms": now_ms + 31_000.0,
            }
            self.assertEqual(client.post("/api/trial/prepare", json=payload).status_code, 200)
            observed = {}

            def refresh():
                observed["refresh_epoch_ms"] = time.time() * 1000.0

            def handler(options):
                observed["handler_options"] = dict(options)
                return {"marker_value": "start-marker"}

            with (
                patch("study_runner.backend.routes.study._refresh_trial_runtime", side_effect=refresh),
                patch("study_runner.backend.routes.study.start_trial_session", side_effect=handler),
                patch(
                    "study_runner.backend.routes.study._require_trial_start_runtime",
                    return_value={"active_session": True, "session_id": "session-test"},
                ),
            ):
                response = client.post("/api/start", json=payload)

        self.assertEqual(response.status_code, 200)
        received = observed["handler_options"]["server_received_epoch_ms"]
        # The public ingress value is rounded to microsecond precision. Allow
        # that sub-microsecond rounding edge when both calls share one clock tick.
        self.assertLessEqual(received, observed["refresh_epoch_ms"] + 0.001)
        self.assertEqual(observed["handler_options"]["source_epoch_ms"], now_ms)


if __name__ == "__main__":
    unittest.main()
