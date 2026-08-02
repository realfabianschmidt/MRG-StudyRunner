from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.integrations.local_emotion_worker import plugin as worker_plugin
from study_runner.integrations.local_emotion_worker import server as worker_server
from study_runner.integrations.plugin_api import IntegrationContext
from study_runner.integrations.tablet_camera_emotion import adapter as camera_adapter
from study_runner.integrations.tablet_camera_emotion.plugin import PLUGIN as CAMERA_EMOTION_PLUGIN


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
        worker_plugin._install_job = {
            "running": False,
            "last_message": "Dependency repair has not been run.",
        }
        worker_plugin._close_log_handle()
        camera_adapter._config = {}
        camera_adapter._history.clear()
        camera_adapter._sequence_state.clear()
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

    def test_sequence_gaps_are_visible_and_replays_are_rejected(self) -> None:
        camera_adapter.initialize(enabled=True, worker_mode="placeholder")
        common = {
            "image": "data:image/jpeg;base64,AAAA",
            "source_instance_id": "capture-a",
            "source_epoch_ms": 1000.0,
        }

        first = camera_adapter.process_frame({**common, "sequence_number": 0})
        gap = camera_adapter.process_frame({**common, "sequence_number": 2})
        duplicate = camera_adapter.process_frame({**common, "sequence_number": 2})
        out_of_order = camera_adapter.process_frame({**common, "sequence_number": 1})

        self.assertTrue(first["accepted"])
        self.assertEqual(first["sequence_diagnostics"]["sequence_status"], "first")
        self.assertTrue(gap["accepted"])
        self.assertEqual(gap["sequence_diagnostics"]["gap_count"], 1)
        self.assertEqual(gap["drop_count"], 1)
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["reason"], "duplicate_sequence")
        self.assertFalse(out_of_order["accepted"])
        self.assertEqual(out_of_order["reason"], "out_of_order_sequence")
        status = camera_adapter.get_status()
        self.assertEqual(status["status"], "degraded")
        self.assertIn("rejected", status["last_message"].lower())

    def test_recorded_emotion_samples_are_exported_through_sidecar_plugin(self) -> None:
        camera_adapter._history.extend(
            [
                {"_epoch": 10.0, "analysis": {"emotion": "neutral"}},
                {"_epoch": 20.0, "analysis": {"emotion": "happy"}},
                {"_epoch": 30.0, "analysis": {"emotion": "sad"}},
            ]
        )
        context = _context({"camera_emotion": {"enabled": True}})

        samples = CAMERA_EMOTION_PLUGIN.export_interval_samples(context, 15.0, 25.0)

        self.assertEqual(samples, [{"_epoch": 20.0, "analysis": {"emotion": "happy"}}])
        self.assertEqual(CAMERA_EMOTION_PLUGIN.sidecar_sensor, "camera_emotion")
        self.assertEqual(
            CAMERA_EMOTION_PLUGIN.sidecar_filename_suffix,
            "camera_emotion_signals",
        )
        self.assertEqual(CAMERA_EMOTION_PLUGIN.sidecar_output_key, "camera_emotion_file")

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

            with patch.object(
                worker_plugin,
                "_asset_is_valid",
                side_effect=lambda path: Path(path) == bundled_asset,
            ):
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
                data_root / "runtime" / "camera_emotion" / "worker" / "deepface_home" / ".deepface" / "weights",
            )
            self.assertEqual(
                Path(worker_plugin._config["raw_log_path"]).parent,
                data_root / "runtime" / "camera_emotion" / "worker" / "logs",
            )

    def test_existing_legacy_worker_cache_is_reused_for_one_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            legacy = data_root / "runtime" / "local_emotion_worker"
            (legacy / "logs").mkdir(parents=True)
            (legacy / "deepface_home").mkdir()
            context = IntegrationContext(
                base_dir=PROJECT_ROOT,
                data_dir=data_root / "saved_results",
                hardware_config={
                    "camera_emotion": {
                        "enabled": True,
                        "worker_mode": "local_worker",
                    }
                },
                local_secrets={},
                local_secrets_file=data_root / "settings" / "local_secrets.json",
            )

            worker_plugin._configure(context)

            self.assertEqual(
                Path(worker_plugin._config["model_cache_dir"]),
                legacy / "deepface_home" / ".deepface" / "weights",
            )
            self.assertEqual(
                Path(worker_plugin._config["raw_log_path"]).parent,
                legacy / "logs",
            )

    def test_packaged_dependency_repair_skips_pip(self) -> None:
        context = _context({"camera_emotion": {"enabled": True, "worker_mode": "local_worker"}})
        worker_plugin._configure(context)

        with patch.object(worker_plugin, "_is_packaged_runtime", return_value=True):
            self.assertTrue(worker_plugin._run_dependency_install(context))

        state = worker_plugin._dependency_install_status()
        self.assertEqual(state["status"], "skipped")
        self.assertIn("Install & Repair Wizard", state["last_message"])

    def test_macos_intel_local_worker_fails_closed_with_remote_action(self) -> None:
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

        with (
            patch.object(worker_plugin.sys, "platform", "darwin"),
            patch.object(worker_plugin.platform, "machine", return_value="x86_64"),
            patch.object(worker_plugin.subprocess, "Popen") as popen,
            patch.object(worker_plugin.subprocess, "run") as run,
        ):
            status = worker_plugin.ensure_started(context)
            dependency_ok = worker_plugin._run_dependency_install(context)

        self.assertEqual(status["status"], "unsupported")
        self.assertFalse(status["runtime_enabled"])
        self.assertEqual(status["supported_modes"], ["remote_worker"])
        self.assertIn("remote_worker", status["last_message"])
        self.assertFalse(dependency_ok)
        self.assertEqual(worker_plugin._dependency_install_status()["status"], "unsupported")
        popen.assert_not_called()
        run.assert_not_called()

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

    def test_missing_model_requires_explicit_operator_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worker_plugin._config = {
                "model_cache_dir": str(root / "cache"),
                "model_assets_dir": str(root / "missing-bundled"),
            }

            result = worker_plugin._run_model_asset_install()

            self.assertFalse(result)
            state = worker_plugin._model_asset_install_status()
            self.assertEqual(state["status"], "attention_required")
            self.assertIn("THIRD_PARTY_NOTICES.md", state["last_message"])
            self.assertFalse(hasattr(worker_plugin, "_download_model_asset"))

    def test_worker_warmup_never_silently_downloads_a_missing_model(self) -> None:
        original_state = dict(worker_server.MODEL_STATE)
        try:
            with patch.object(worker_server, "_asset_is_valid", return_value=False):
                worker_server._warmup_deepface()

            self.assertTrue(worker_server.MODEL_STATE["model_checked"])
            self.assertFalse(worker_server.MODEL_STATE["model_ready"])
            self.assertEqual(worker_server.MODEL_STATE["model_error_class"], "model_file_missing")
            self.assertIn("Automatic model downloads are disabled", worker_server.MODEL_STATE["model_error"])
        finally:
            worker_server.MODEL_STATE.clear()
            worker_server.MODEL_STATE.update(original_state)


def _context(hardware_config: dict) -> IntegrationContext:
    return IntegrationContext(
        base_dir=PROJECT_ROOT,
        data_dir=PROJECT_ROOT / "saved_results",
        hardware_config=hardware_config,
        local_secrets={},
        local_secrets_file=PROJECT_ROOT / "local_secrets.json",
    )


class CrashedWorkerProcess:
    def poll(self):
        return 1


class WorkerAutoRestartTests(unittest.TestCase):
    def setUp(self) -> None:
        worker_plugin._config = {"auto_restart": True}
        worker_plugin._worker_restart_count = 0
        worker_plugin._last_worker_restart_at = 0.0
        worker_plugin._monitor_context = object()

    def tearDown(self) -> None:
        worker_plugin._process = None
        worker_plugin._monitor_context = None
        worker_plugin._worker_restart_count = 0

    def test_monitor_restarts_crashed_worker(self) -> None:
        worker_plugin._process = CrashedWorkerProcess()
        restarts: list[object] = []

        def fake_start(context):
            restarts.append(context)
            with worker_plugin._lock:
                worker_plugin._process = None  # ends the monitor loop

        original_start = worker_plugin._start
        worker_plugin._start = fake_start
        try:
            with patch.object(worker_plugin.time, "sleep", lambda _seconds: None):
                worker_plugin._watch_worker()
        finally:
            worker_plugin._start = original_start

        self.assertEqual(len(restarts), 1)
        self.assertEqual(worker_plugin._worker_restart_count, 1)

    def test_monitor_gives_up_after_attempt_limit(self) -> None:
        worker_plugin._process = CrashedWorkerProcess()
        worker_plugin._worker_restart_count = 3
        restarts: list[object] = []

        original_start = worker_plugin._start
        worker_plugin._start = lambda context: restarts.append(context)
        try:
            with patch.object(worker_plugin.time, "sleep", lambda _seconds: None):
                worker_plugin._watch_worker()
        finally:
            worker_plugin._start = original_start

        self.assertEqual(restarts, [])

    def test_deliberate_stop_ends_monitor_without_restart(self) -> None:
        process = FakeWorkerProcess()
        worker_plugin._process = process

        restarts: list[object] = []
        original_start = worker_plugin._start
        worker_plugin._start = lambda context: restarts.append(context)
        try:
            worker_plugin._stop_process()
            with patch.object(worker_plugin.time, "sleep", lambda _seconds: None):
                worker_plugin._watch_worker()
        finally:
            worker_plugin._start = original_start

        self.assertTrue(process.terminated)
        self.assertEqual(restarts, [])


if __name__ == "__main__":
    unittest.main()
