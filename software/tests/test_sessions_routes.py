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
from study_runner.backend.services.sessions_index_service import (
    list_sessions,
    min_max_envelope,
)
from study_runner.backend.services import sessions_index_service


class SessionsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.temp_dir.name)
        self.data_dir = self.storage_root / "saved_results"
        participant_dir = self.data_dir / "study-a" / "p01"
        participant_dir.mkdir(parents=True)

        self._write_json(
            participant_dir / "p01.json",
            {
                "study_id": "study-a",
                "participant_id": "p01",
                "session_id": "session-1",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:10:00Z",
                "answers": {"q1": "first"},
                "answer_details": [{"question_index": 1}],
            },
        )
        self._write_json(
            participant_dir / "p01_mr60_signals.json",
            {
                "sensor": "mr60",
                "study_id": "study-a",
                "participant_id": "p01",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:10:00Z",
                "sample_count": 3,
                "samples": [
                    {"server_received_epoch": 1.0, "heartRate": 70},
                    {"server_received_epoch": 2.0, "heartRate": 90},
                    {"server_received_epoch": 3.0, "heartRate": 60},
                ],
            },
        )
        self._write_json(
            participant_dir / "p01_2.json",
            {
                "study_id": "study-a",
                "participant_id": "p01",
                "session_id": "session-2",
                "timestamp_start": "2026-01-02T10:00:00Z",
                "timestamp_end": "2026-01-02T10:20:00Z",
                "answers": {"q1": "second", "q2": 4},
                "answer_details": [{"question_index": 1}, {"question_index": 2}],
                "recovered": True,
            },
        )
        self._write_json(
            participant_dir / "p01_camera_emotion_signals_2.json",
            {
                "sensor": "camera_emotion",
                "study_id": "study-a",
                "participant_id": "p01",
                "timestamp_start": "2026-01-02T10:00:00Z",
                "timestamp_end": "2026-01-02T10:20:00Z",
                "sample_count": 1,
                "samples": [
                    {
                        "_epoch": 4.0,
                        "analysis": {"scores": {"happy": 0.8, "sad": 0.2}},
                    }
                ],
            },
        )
        with patch.dict(
            os.environ,
            {
                "STUDY_RUNNER_DATA_DIR": str(self.storage_root),
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
            },
            clear=False,
        ):
            self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_returns_each_result_file_as_a_distinct_session(self) -> None:
        response = self.client.get("/api/admin/sessions")

        self.assertEqual(response.status_code, 200)
        sessions = response.get_json()
        self.assertEqual([item["result_file"] for item in sessions], ["p01_2.json", "p01.json"])
        self.assertEqual(sessions[0]["session_id"], "session-2")
        self.assertEqual(sessions[0]["answers_count"], 2)
        self.assertTrue(sessions[0]["recovered"])
        self.assertEqual(
            {item["name"] for item in sessions[0]["files"]},
            {"p01_2.json", "p01_camera_emotion_signals_2.json"},
        )

    def test_detail_uses_explicit_result_file_and_returns_sidecar_metadata(self) -> None:
        response = self.client.get(
            "/api/admin/sessions/study-a/p01",
            query_string={"result_file": "p01.json"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["result"]["answers"]["q1"], "first")
        self.assertEqual(payload["sidecars"][0]["sensor"], "mr60")
        self.assertEqual(payload["sidecars"][0]["sample_count"], 3)
        self.assertNotIn("samples", payload["sidecars"][0])

    def test_signal_route_selects_session_and_bounds_payload(self) -> None:
        response = self.client.get(
            "/api/admin/sessions/study-a/p01/signals",
            query_string={
                "result_file": "p01.json",
                "sensor": "mr60",
                "max_points": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sample_count"], 3)
        self.assertEqual(payload["mode"], "min_max_envelope")
        self.assertEqual(len(payload["points"]), 2)
        self.assertLessEqual(
            min(point["min"]["heartRate"] for point in payload["points"]),
            60,
        )
        self.assertGreaterEqual(
            max(point["max"]["heartRate"] for point in payload["points"]),
            90,
        )

    def test_invalid_selectors_and_missing_sensor_are_rejected(self) -> None:
        invalid_file = self.client.get(
            "/api/admin/sessions/study-a/p01",
            query_string={"result_file": "../p01.json"},
        )
        missing_sensor = self.client.get(
            "/api/admin/sessions/study-a/p01/signals",
            query_string={"result_file": "p01.json"},
        )
        invalid_limit = self.client.get(
            "/api/admin/sessions/study-a/p01/signals",
            query_string={
                "result_file": "p01.json",
                "sensor": "mr60",
                "max_points": "unbounded",
            },
        )

        self.assertEqual(invalid_file.status_code, 400)
        self.assertEqual(missing_sensor.status_code, 400)
        self.assertEqual(invalid_limit.status_code, 400)

    def test_index_cache_reuses_json_scan_until_a_file_changes(self) -> None:
        sessions_index_service._INDEX_CACHE.clear()
        original_reader = sessions_index_service._read_json_object
        with patch.object(
            sessions_index_service,
            "_read_json_object",
            wraps=original_reader,
        ) as reader:
            first = list_sessions(self.data_dir)
            reads_after_first_scan = reader.call_count
            second = list_sessions(self.data_dir)

            self.assertEqual(first, second)
            self.assertGreater(reads_after_first_scan, 0)
            self.assertEqual(reader.call_count, reads_after_first_scan)

            result_file = self.data_dir / "study-a" / "p01" / "p01_2.json"
            payload = json.loads(result_file.read_text(encoding="utf-8"))
            payload["answers"]["q3"] = "changed"
            self._write_json(result_file, payload)
            refreshed = list_sessions(self.data_dir)

            self.assertGreater(reader.call_count, reads_after_first_scan)
            self.assertEqual(refreshed[0]["answers_count"], 3)

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")


class MinMaxEnvelopeTests(unittest.TestCase):
    def test_short_and_empty_inputs_stay_raw(self) -> None:
        self.assertEqual(min_max_envelope([], 20), {"mode": "raw", "points": []})
        samples = [{"server_received_epoch": 1.0, "heartRate": 72}]
        self.assertEqual(
            min_max_envelope(samples, 20),
            {"mode": "raw", "points": samples},
        )

    def test_nested_channel_extremes_are_preserved(self) -> None:
        samples = [
            {
                "server_received_epoch": float(index),
                "payload": {"alpha": value, "attention": 1.0 - value},
            }
            for index, value in enumerate((0.3, 0.9, 0.1, 0.7))
        ]

        result = min_max_envelope(samples, 1)

        self.assertEqual(result["mode"], "min_max_envelope")
        self.assertEqual(result["points"][0]["min"]["payload.alpha"], 0.1)
        self.assertEqual(result["points"][0]["max"]["payload.alpha"], 0.9)
        self.assertAlmostEqual(result["points"][0]["min"]["payload.attention"], 0.1)
        self.assertAlmostEqual(result["points"][0]["max"]["payload.attention"], 0.9)


if __name__ == "__main__":
    unittest.main()
