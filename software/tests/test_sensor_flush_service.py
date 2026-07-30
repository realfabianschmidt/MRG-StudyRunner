"""Periodic sensor-history export used to recover data after a crash."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.sensor_flush_service import (
    SensorFlushService,
    discard_session_flush_files,
)


class FakeSessionStore:
    def __init__(self, sessions):
        self._sessions = sessions

    def list_active(self):
        return list(self._sessions)


class FakeApp:
    def __init__(self, data_dir: Path, sessions, hardware_config=None):
        self.config = {
            "BASE_DIR": data_dir.parent,
            "DATA_DIR": data_dir,
            "LOCAL_SECRETS": {},
            "LOCAL_SECRETS_FILE": data_dir.parent / "local_secrets.json",
            "ACTIVE_STUDY_HARDWARE_CONFIG": hardware_config,
            "SESSION_STORE": FakeSessionStore(sessions),
        }


FAKE_EXPORT = [
    {
        "plugin_key": "mr60",
        "sensor": "mr60",
        "filename_suffix": "mr60_signals",
        "output_key": "mr60_file",
        "samples": [{"server_received_epoch": 1_000_050.0, "heartRate": 72}],
    }
]


class SensorFlushServiceTests(unittest.TestCase):
    def test_flush_once_writes_one_file_per_exported_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "saved_results"
            session = {"session_id": "session-1", "study_id": "study-a", "participant_id": "p01", "started_at_epoch": 1_000_000.0}
            app = FakeApp(data_dir, [session], hardware_config={"mr60": {"enabled": True}})
            service = SensorFlushService(app, clock=lambda: 1_000_100.0)

            with patch("study_runner.backend.services.sensor_flush_service.export_interval_sidecars", return_value=FAKE_EXPORT):
                written = service.flush_once()

            self.assertEqual(written, 1)
            flush_path = data_dir / "study-a" / "_flush" / "session-1_mr60_signals.json"
            self.assertTrue(flush_path.is_file())
            payload = json.loads(flush_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["sensor"], "mr60")
            self.assertEqual(payload["session_id"], "session-1")
            self.assertEqual(len(payload["samples"]), 1)

    def test_flush_once_does_nothing_without_an_active_study(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "saved_results"
            session = {"session_id": "session-1", "study_id": "study-a", "participant_id": "p01", "started_at_epoch": 1_000_000.0}
            app = FakeApp(data_dir, [session], hardware_config=None)
            service = SensorFlushService(app, clock=lambda: 1_000_100.0)

            with patch("study_runner.backend.services.sensor_flush_service.export_interval_sidecars", return_value=FAKE_EXPORT):
                written = service.flush_once()

            self.assertEqual(written, 0)
            self.assertFalse((data_dir / "study-a" / "_flush").exists())

    def test_flush_once_skips_sessions_without_a_start_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "saved_results"
            session = {"session_id": "session-1", "study_id": "study-a", "participant_id": "p01"}
            app = FakeApp(data_dir, [session], hardware_config={"mr60": {"enabled": True}})
            service = SensorFlushService(app, clock=lambda: 1_000_100.0)

            with patch("study_runner.backend.services.sensor_flush_service.export_interval_sidecars", return_value=FAKE_EXPORT):
                written = service.flush_once()

            self.assertEqual(written, 0)

    def test_discard_session_flush_files_removes_only_that_sessions_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "saved_results"
            flush_dir = data_dir / "study-a" / "_flush"
            flush_dir.mkdir(parents=True)
            (flush_dir / "session-1_mr60_signals.json").write_text("{}", encoding="utf-8")
            (flush_dir / "session-2_mr60_signals.json").write_text("{}", encoding="utf-8")

            discard_session_flush_files(data_dir, "study-a", "session-1")

            remaining = {path.name for path in flush_dir.glob("*.json")}
            self.assertEqual(remaining, {"session-2_mr60_signals.json"})

    def test_discard_session_flush_files_is_a_no_op_when_nothing_was_flushed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "saved_results"
            discard_session_flush_files(data_dir, "study-a", "session-1")  # must not raise


if __name__ == "__main__":
    unittest.main()
