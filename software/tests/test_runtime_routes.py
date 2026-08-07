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


class RuntimeRoutesTests(unittest.TestCase):
    def test_health_and_runtime_info_routes(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_APP_MODE": "packaged",
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_PORT": "3123",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            health = client.get("/api/health")
            runtime_info = client.get("/api/runtime-info")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(runtime_info.status_code, 200)

        health_payload = health.get_json()
        info_payload = runtime_info.get_json()
        self.assertEqual(health_payload["status"], "running")
        self.assertEqual(health_payload["app_mode"], "packaged")
        self.assertEqual(info_payload["port"], 3123)
        self.assertEqual(info_payload["admin_url"], "https://localhost:3123/admin")
        self.assertTrue(info_payload["participant_url"].startswith("https://"))
        self.assertTrue(info_payload["uses_external_storage"])

    def test_packaged_restart_returns_packaged_message(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_APP_MODE": "packaged",
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            response = app.test_client().post("/api/admin/restart")

        payload = response.get_json()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(payload["ok"])
        self.assertIn("packaged builds", payload["error"])

    def test_study_session_start_requires_participant_id(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            response = app.test_client().post(
                "/api/study/session/start",
                json={"study_id": "test", "participant_id": ""},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("Participant ID", payload["error"])

    def test_admin_study_run_start_gates_new_tablet_flow_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            saved = client.post(
                "/api/config",
                json={
                    "study_id": "study-a",
                    "questions": [
                        {"type": "participant-id"},
                        {"type": "likert", "prompt": "How do you feel?"},
                        {"type": "finish"},
                    ],
                },
            )
            blocked = client.post(
                "/api/study/session/start",
                json={
                    "study_id": "study-a",
                    "participant_id": "p01",
                    "require_admin_start": True,
                },
            )
            heartbeat = client.post(
                "/api/study-client/heartbeat",
                json={
                    "client_id": "tablet-1",
                    "study_id": "study-a",
                    "waiting_for_admin_start": True,
                },
            )
            started = client.post("/api/admin/study-run/start", json={})
            allowed = client.post(
                "/api/study/session/start",
                json={
                    "client_id": "tablet-1",
                    "study_id": "study-a",
                    "participant_id": "p01",
                    "require_admin_start": True,
                },
            )

            with patch.dict(os.environ, env, clear=False):
                restarted_app = create_app()
            persisted = restarted_app.test_client().get("/api/admin/study-run")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.get_json()["run_state"]["status"], "running")
        self.assertEqual(started.get_json()["run_state"]["active_client_id"], "tablet-1")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.get_json()["study_run_state"]["status"], "running")
        self.assertEqual(persisted.status_code, 200)
        self.assertEqual(persisted.get_json()["run_state"]["status"], "running")

    def test_admin_study_run_load_returns_config_and_waiting_state(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            saved = client.post(
                "/api/config",
                json={
                    "study_id": "study-a",
                    "questions": [
                        {"type": "participant-id"},
                        {"type": "finish"},
                    ],
                },
            )
            client.post(
                "/api/study-client/heartbeat",
                json={"client_id": "tablet-1", "study_id": "study-a", "waiting_for_admin_start": True},
            )
            started = client.post("/api/admin/study-run/start", json={})
            loaded = client.post("/api/admin/study-run/load", json={"id": "study-a"})
            config = client.get("/api/config")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(started.get_json()["run_state"]["status"], "running")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.get_json()["config"]["study_id"], "study-a")
        self.assertEqual(loaded.get_json()["run_state"]["status"], "loaded")
        self.assertEqual(config.get_json()["_runtime"]["study_run_state"]["status"], "loaded")

    def test_admin_study_run_start_requires_exactly_one_matching_tablet(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            saved = client.post(
                "/api/config",
                json={"study_id": "study-a", "questions": [{"type": "participant-id"}, {"type": "finish"}]},
            )
            no_tablet = client.post("/api/admin/study-run/start", json={})
            client.post(
                "/api/study-client/heartbeat",
                json={"client_id": "tablet-1", "study_id": "study-a", "waiting_for_admin_start": True},
            )
            client.post(
                "/api/study-client/heartbeat",
                json={"client_id": "tablet-2", "study_id": "study-a", "waiting_for_admin_start": True},
            )
            conflict = client.post("/api/admin/study-run/start", json={})

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(no_tablet.status_code, 409)
        self.assertEqual(no_tablet.get_json()["tablet_gate"]["status"], "waiting_for_tablet")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["tablet_gate"]["status"], "conflict")

    def test_non_assigned_tablet_is_blocked_after_admin_start(self) -> None:
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
                "/api/config",
                json={"study_id": "study-a", "questions": [{"type": "participant-id"}, {"type": "finish"}]},
            )
            client.post(
                "/api/study-client/heartbeat",
                json={"client_id": "tablet-1", "study_id": "study-a", "waiting_for_admin_start": True},
            )
            started = client.post("/api/admin/study-run/start", json={})
            wrong_heartbeat = client.post(
                "/api/study-client/heartbeat",
                json={"client_id": "tablet-2", "study_id": "study-a", "waiting_for_admin_start": True},
            )
            wrong_start = client.post(
                "/api/study/session/start",
                json={
                    "client_id": "tablet-2",
                    "study_id": "study-a",
                    "participant_id": "p02",
                    "require_admin_start": True,
                    "study_run_id": started.get_json()["run_state"]["run_id"],
                },
            )
            allowed = client.post(
                "/api/study/session/start",
                json={
                    "client_id": "tablet-1",
                    "study_id": "study-a",
                    "participant_id": "p01",
                    "require_admin_start": True,
                    "study_run_id": started.get_json()["run_state"]["run_id"],
                },
            )

        self.assertEqual(started.status_code, 200)
        self.assertEqual(wrong_heartbeat.status_code, 200)
        self.assertEqual(wrong_heartbeat.get_json()["study_run_state"]["status"], "blocked")
        self.assertEqual(wrong_start.status_code, 409)
        self.assertEqual(allowed.status_code, 200)

    def test_active_study_sensor_toggle_sets_temporary_override(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            app.config["HARDWARE_CONFIG"] = {"brainbit": {"enabled": False}}
            app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {"brainbit": {"enabled": True}}
            response = app.test_client().post(
                "/api/admin/plugins/brainbit/enabled",
                json={"enabled": False},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["study_controlled"])
        self.assertTrue(payload["temporary_override"])
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["session_overrides"]["brainbit"])

    def test_internal_lsl_recording_provider_is_not_a_toggleable_plugin(self) -> None:
        """markers/clock_diagnostics are recording code, not plugins -- there is
        nothing at this route for "lsl" to reach, let alone disable."""
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            app.config["HARDWARE_CONFIG"] = {"lsl": {"enabled": True}}
            app.config["ACTIVE_STUDY_HARDWARE_CONFIG"] = {"lsl": {"enabled": True}}
            response = app.test_client().post(
                "/api/admin/plugins/lsl/enabled",
                json={"enabled": False},
            )

            active_config = app.config["ACTIVE_STUDY_HARDWARE_CONFIG"]

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("Unknown plugin", payload["error"])
        self.assertTrue(active_config["lsl"]["enabled"])

    def test_study_runtime_reports_session_override_effective_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            app.config["SESSION_SENSOR_OVERRIDES"] = {"camera_emotion": True}
            response = app.test_client().get("/api/study/runtime")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["sensor_runtime"]["override_active"]["camera_emotion"])
        self.assertTrue(payload["sensor_runtime"]["effective"]["camera_emotion"])

    def test_admin_status_includes_coordinator_and_clock_sync(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            heartbeat = client.post(
                "/api/study-client/heartbeat",
                json={
                    "client_id": "tablet-1",
                    "study_id": "study-a",
                    "clock_offset_ms": 12.5,
                    "clock_sync_rtt_ms": 24,
                    "plugin_status": {
                        "fixture_sensor": {
                            "state": "warning",
                            "last_error": "sample gap",
                            "active": False,
                            "nested_untrusted": {"ignored": True},
                        }
                    },
                },
            )
            status = client.get("/api/admin/status")

        payload = status.get_json()
        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(status.status_code, 200)
        self.assertIn("sensor_coordinator", payload)
        self.assertIn("sample_metadata_model", payload["sensor_coordinator"])
        self.assertIn("recording_infrastructure", payload)
        self.assertIn("canonical_xdf", payload["recording_infrastructure"])
        self.assertIn("supports_merge", payload["recording_infrastructure"])
        self.assertEqual(payload["plugins"]["camera_emotion"]["manifest"]["poll_interval_ms"], 1000)
        self.assertEqual(payload["clock_sync"]["sources"]["tablet-1"]["median_offset_ms"], 12.5)
        client_status = payload["study_clients"]["clients"][0]["plugin_status"]["fixture_sensor"]
        self.assertEqual(client_status["state"], "warning")
        self.assertEqual(client_status["last_error"], "sample gap")
        self.assertFalse(client_status["active"])
        self.assertNotIn("nested_untrusted", client_status)
        self.assertNotIn("camera_permission", payload["study_clients"]["clients"][0])

    def test_emotion_worker_repair_runtime_route_reports_package_and_model_state(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            with patch(
                "study_runner.plugins.camera_emotion.worker.plugin.repair_runtime",
                return_value={
                    "dependency_install": {"status": "running"},
                    "model_asset_install": {"status": "queued"},
                },
            ):
                response = app.test_client().post("/api/admin/emotion-worker/repair-runtime")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dependency_install"]["status"], "running")
        self.assertEqual(payload["model_asset_install"]["status"], "queued")

    def test_camera_live_status_replaces_separate_preview_page(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            live_status = client.get("/api/admin/camera/live/status")
            preview_page = client.get("/camera-preview")

        payload = live_status.get_json()
        self.assertEqual(live_status.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("available", payload)
        self.assertEqual(live_status.headers["Deprecation"], "true")
        self.assertIn("successor-version", live_status.headers["Link"])
        self.assertEqual(preview_page.status_code, 404)

    def test_create_shortcut_route_returns_service_result(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            with patch(
                "study_runner.backend.routes.admin.create_desktop_shortcut",
                return_value={"ok": True, "platform": "windows", "path": "C:/Users/test/Desktop/Study Runner.lnk"},
            ):
                response = app.test_client().post("/api/admin/system/create-shortcut")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("Study Runner", payload["path"])

    def test_nextcloud_password_stays_backend_local_and_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            client = app.test_client()
            saved = client.post(
                "/api/hardware-config",
                json={
                    "nextcloud": {
                        "password": "share-secret",
                    }
                },
            )
            returned = client.get("/api/hardware-config").get_json()
            secrets_file = Path(data_dir) / "settings" / "local_secrets.json"
            secrets = json.loads(secrets_file.read_text(encoding="utf-8"))
            hardware_file = Path(data_dir) / "settings" / "hardware_settings.json"
            hardware = json.loads(hardware_file.read_text(encoding="utf-8"))

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(secrets["nextcloud"]["password"], "share-secret")
        self.assertNotIn("password", hardware["nextcloud"])
        self.assertEqual(returned["nextcloud"]["password"], "")
        self.assertTrue(returned["nextcloud"]["password_configured"])
        self.assertNotIn("share-secret", json.dumps(returned))

    def test_nextcloud_test_connection_is_a_declared_admin_action(self) -> None:
        """Testing a connection has no route of its own -- plugins/nextcloud_upload/
        plugin.py declares it as an admin action and this is the one generic
        dispatch every plugin's actions go through."""
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                app = create_app()

            with patch(
                "study_runner.backend.services.delivery.nextcloud_service.test_connection",
                return_value={"ok": True, "endpoint": "dav"},
            ) as connection_test:
                response = app.test_client().post(
                    "/api/admin/plugins/nextcloud/actions/test_connection",
                    json={
                        "share_link": "https://cloud.example/s/token",
                        "password": "temporary-secret",
                    },
                )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["result"]["ok"])
        connection_test.assert_called_once_with(
            "https://cloud.example/s/token",
            password="temporary-secret",
            timeout_seconds=10,
        )
        self.assertNotIn("temporary-secret", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
