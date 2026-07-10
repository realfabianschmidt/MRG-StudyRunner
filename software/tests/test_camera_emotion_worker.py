from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import urllib.error
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.integrations.local_emotion_worker import plugin as worker_plugin
from study_runner.integrations.local_emotion_worker import server as worker_server
from study_runner.integrations.plugin_api import IntegrationContext
from study_runner.integrations.tablet_camera_emotion import adapter as camera_adapter


class FakeWorkerProcess:
    pid = 24680

    def __init__(self) -> None:
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        self.terminated = True
        return 0

    def kill(self) -> None:
        self.terminated = True


class CameraEmotionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        worker_plugin._config = {}
        worker_plugin._process = None
        worker_plugin._model_job = {
            "running": False,
            "last_message": "Model asset repair has not been run.",
        }
        worker_plugin._close_log_handle()
        camera_adapter._config = {}
        camera_adapter._history.clear()
        camera_adapter._preview_state = {
            "available": False,
            "last_message": "No tablet camera live frame received yet.",
        }
        camera_adapter._latest_state = {
            "status": "not_configured",
            "latest": {},
            "last_message": "Camera affect adapter has not been configured.",
        }

    def tearDown(self) -> None:
        worker_plugin._stop_process()

    def test_camera_worker_error_is_returned_without_placeholder_success(self) -> None:
        camera_adapter.initialize(enabled=True, worker_mode="local_worker", emotion_worker_url="")

        result = camera_adapter.process_frame({"image": "", "image_format": "jpeg"})

        self.assertTrue(result["accepted"])
        self.assertEqual(result["analysis"]["emotion"], "unknown")
        self.assertIn("error", result["analysis"])
        self.assertEqual(camera_adapter.get_status()["status"], "failed")

    def test_preview_frame_is_not_added_to_study_history(self) -> None:
        camera_adapter.initialize(enabled=True, worker_mode="placeholder")

        result = camera_adapter.process_frame(
            {
                "preview": True,
                "image": "data:image/jpeg;base64,AAAA",
                "image_format": "jpeg",
                "width": 320,
                "height": 240,
                "emotion": "happy",
                "face_detected": True,
            }
        )

        self.assertTrue(result["accepted"])
        self.assertTrue(result["preview"])
        self.assertFalse(result["active_phase"])
        self.assertEqual(len(camera_adapter._history), 0)

        preview_status = camera_adapter.get_preview_status()
        self.assertTrue(preview_status["available"])
        self.assertEqual(preview_status["latest"]["analysis"]["emotion"], "happy")
        self.assertEqual(preview_status["latest"]["frame"]["width"], 320)

    def test_worker_plugin_start_stop_with_mocked_process(self) -> None:
        context = _context(
            {
                "camera_emotion": {
                    "enabled": True,
                    "worker_mode": "local_worker",
                    "emotion_worker_url": "http://127.0.0.1:3001",
                    "emotion_worker": {"auto_start": True},
                }
            }
        )
        fake_process = FakeWorkerProcess()
        original_popen = worker_plugin.subprocess.Popen
        original_probe = worker_plugin._probe_worker
        try:
            worker_plugin.subprocess.Popen = lambda *args, **kwargs: fake_process
            worker_plugin._probe_worker = lambda url, timeout: (
                ({}, "connection refused")
                if fake_process.terminated
                else ({"ready": True, "model_ready": True, "message": "ok"}, None)
            )

            status = worker_plugin.ensure_started(context)

            self.assertEqual(status["status"], "connected")
            self.assertTrue(status["runtime_enabled"])

            stopped = worker_plugin.stop_worker(context)

            self.assertEqual(stopped["status"], "unreachable")
            self.assertTrue(fake_process.terminated)
        finally:
            worker_plugin.subprocess.Popen = original_popen
            worker_plugin._probe_worker = original_probe

    def test_packaged_worker_starts_through_current_executable(self) -> None:
        context = _context(
            {
                "camera_emotion": {
                    "enabled": True,
                    "worker_mode": "local_worker",
                    "emotion_worker_url": "http://127.0.0.1:3001",
                    "emotion_worker": {"auto_start": True},
                }
            }
        )
        fake_process = FakeWorkerProcess()
        captured: dict[str, object] = {}
        original_popen = worker_plugin.subprocess.Popen
        original_probe = worker_plugin._probe_worker
        try:
            def fake_popen(command, **kwargs):
                captured["command"] = command
                captured["cwd"] = kwargs.get("cwd")
                captured["env"] = kwargs.get("env")
                return fake_process

            worker_plugin.subprocess.Popen = fake_popen
            worker_plugin._probe_worker = lambda url, timeout: ({"ready": True, "model_ready": True}, None)
            with patch.object(worker_plugin, "_is_packaged_runtime", return_value=True), patch.object(sys, "executable", str(PROJECT_ROOT / "dist" / "study-runner-server.exe")):
                status = worker_plugin.ensure_started(context)

            self.assertEqual(status["status"], "connected")
            command = captured["command"]
            self.assertIsInstance(command, list)
            self.assertEqual(command[0], str(PROJECT_ROOT / "dist" / "study-runner-server.exe"))
            self.assertIn("--emotion-worker", command)
            self.assertEqual(captured["cwd"], str((PROJECT_ROOT / "dist").resolve()))
            env = captured["env"]
            self.assertIsInstance(env, dict)
            self.assertIn("STUDY_RUNNER_DATA_DIR", env)
        finally:
            worker_plugin.subprocess.Popen = original_popen
            worker_plugin._probe_worker = original_probe

    def test_worker_status_classifies_deepface_model_download_failure(self) -> None:
        context = _context(
            {
                "camera_emotion": {
                    "enabled": True,
                    "worker_mode": "local_worker",
                    "emotion_worker_url": "http://127.0.0.1:3001",
                }
            }
        )
        original_probe = worker_plugin._probe_worker
        try:
            worker_plugin._probe_worker = lambda url, timeout: (
                {
                    "ready": True,
                    "model_ready": False,
                    "model_error": (
                        "An exception occurred while downloading "
                        "facial_expression_model_weights.h5 from https://example.invalid/model.h5"
                    ),
                },
                None,
            )

            status = worker_plugin.PLUGIN.get_status(context)

            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["model_error_class"], "model_download_failed")
            self.assertIn("model weights", status["last_message"])
        finally:
            worker_plugin._probe_worker = original_probe

    def test_model_asset_install_copies_bundled_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_dir = root / "cache"
            bundled_dir = root / "bundled"
            bundled_dir.mkdir()
            bundled_asset = bundled_dir / worker_plugin.DEEPFACE_EMOTION_MODEL["name"]
            bundled_asset.write_bytes(b"0" * (worker_plugin.DEEPFACE_EMOTION_MODEL["min_bytes"] + 1))
            worker_plugin._config = {
                "model_cache_dir": str(cache_dir),
                "model_assets_dir": str(bundled_dir),
            }

            result = worker_plugin._run_model_asset_install()

            self.assertTrue(result)
            self.assertTrue((cache_dir / worker_plugin.DEEPFACE_EMOTION_MODEL["name"]).exists())
            self.assertEqual(worker_plugin._model_asset_install_status()["status"], "completed")

    def test_default_model_cache_uses_data_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            context = IntegrationContext(
                base_dir=PROJECT_ROOT,
                data_dir=data_root / "saved_results",
                hardware_config={
                    "camera_emotion": {
                        "enabled": True,
                        "worker_mode": "local_worker",
                        "emotion_worker_url": "http://127.0.0.1:3001",
                    }
                },
                local_secrets={},
                local_secrets_file=data_root / "settings" / "local_secrets.json",
            )

            worker_plugin._configure(context)

            self.assertEqual(
                Path(worker_plugin._config["model_cache_dir"]),
                data_root / "runtime" / "local_emotion_worker" / "deepface_home" / ".deepface" / "weights",
            )
            self.assertEqual(
                Path(worker_plugin._config["raw_log_path"]).parent,
                data_root / "runtime" / "local_emotion_worker" / "logs",
            )

    def test_packaged_dependency_repair_skips_pip(self) -> None:
        context = _context({"camera_emotion": {"enabled": True, "worker_mode": "local_worker"}})
        worker_plugin._configure(context)

        with patch.object(worker_plugin, "_is_packaged_runtime", return_value=True):
            self.assertTrue(worker_plugin._run_dependency_install(context))

        state = worker_plugin._dependency_install_status()
        self.assertEqual(state["status"], "skipped")
        self.assertIn("Install & Repair Wizard", state["last_message"])

    def test_worker_self_test_returns_success_when_warmup_is_ready(self) -> None:
        original_state = dict(worker_server.MODEL_STATE)
        try:
            with patch.object(worker_server, "_prepare_deepface_runtime"), patch.object(worker_server, "_warmup_deepface") as warmup:
                def mark_ready() -> None:
                    worker_server.MODEL_STATE["model_checked"] = True
                    worker_server.MODEL_STATE["model_ready"] = True
                    worker_server.MODEL_STATE["model_error"] = None

                warmup.side_effect = mark_ready
                self.assertEqual(worker_server.self_test_main([]), 0)
        finally:
            worker_server.MODEL_STATE.clear()
            worker_server.MODEL_STATE.update(original_state)

    def test_model_asset_download_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worker_plugin._config = {
                "model_cache_dir": str(root / "cache"),
                "model_assets_dir": str(root / "missing-bundled"),
            }
            original_urlopen = worker_plugin.urllib.request.urlopen
            try:
                worker_plugin.urllib.request.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(
                    urllib.error.URLError("blocked")
                )

                result = worker_plugin._run_model_asset_install()

                self.assertFalse(result)
                state = worker_plugin._model_asset_install_status()
                self.assertEqual(state["status"], "failed")
                self.assertIn("blocked", state["last_message"])
            finally:
                worker_plugin.urllib.request.urlopen = original_urlopen


def _context(hardware_config: dict) -> IntegrationContext:
    return IntegrationContext(
        base_dir=PROJECT_ROOT,
        data_dir=PROJECT_ROOT / "saved_results",
        hardware_config=hardware_config,
        local_secrets={},
        local_secrets_file=PROJECT_ROOT / "local_secrets.json",
    )


if __name__ == "__main__":
    unittest.main()
