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
    _stream_descriptor,
    _validate_window,
    list_sessions,
    min_max_envelope,
)
from study_runner.backend.services import sessions_index_service


class SessionsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.temp_dir.name)
        self.data_dir = self.storage_root / "saved_results"

        # This is deliberately a valid legacy result. It must remain on disk,
        # but the v3 browser must never index or open it.
        legacy_dir = self.data_dir / "study-a" / "p01"
        legacy_dir.mkdir(parents=True)
        self._write_json(
            legacy_dir / "p01.json",
            {
                "study_id": "study-a",
                "participant_id": "p01",
                "session_id": "legacy-session",
                "answers": {"q1": "legacy"},
            },
        )

        self.session_one = self._canonical_session(
            "20260101T100000Z__session-1",
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
        self.session_two = self._canonical_session(
            "20260102T100000Z__session-2",
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

    def _canonical_session(self, folder: str, result: dict) -> Path:
        root = self.data_dir / "study-a" / "participants" / "p01" / "sessions" / folder
        (root / "derived").mkdir(parents=True)
        self._write_json(root / "result.json", result)
        self._write_json(
            root / "session-identity.json",
            {
                "study_id": result["study_id"],
                "participant_id": result["participant_id"],
                "session_id": result["session_id"],
            },
        )
        self._write_json(root / "COMPLETE.json", {"status": "completed", "published_at": result["timestamp_end"]})
        self._write_json(
            root / "manifest.json",
            {
                "quality_status": "valid",
                "artifacts": [
                    {"path": "result.json", "role": "result"},
                    {"path": "derived/session.xdf", "role": "merged_xdf"},
                ],
            },
        )
        (root / "derived" / "session.xdf").write_bytes(b"fixture-xdf")
        return root

    @staticmethod
    def _fixture_streams(session_root: Path) -> list[dict]:
        if session_root.name.endswith("session-1"):
            return [
                {
                    "stream_key": "mr60.vitals",
                    "name": "MR60 Vitals",
                    "plugin_key": "mr60",
                    "nominal_rate_hz": 1.0,
                    "timestamps": [1.0, 2.0, 3.0],
                    "samples": [{"heartRate": 70}, {"heartRate": 90}, {"heartRate": 60}],
                }
            ]
        return [
            {
                "stream_key": "camera.emotion",
                "name": "Camera Emotion",
                "plugin_key": "camera_emotion",
                "nominal_rate_hz": 1.0,
                "timestamps": [4.0],
                "samples": [{"happy": 0.8, "sad": 0.2}],
            }
        ]

    def test_list_only_returns_canonical_marked_sessions(self) -> None:
        response = self.client.get("/api/admin/sessions")

        self.assertEqual(response.status_code, 200)
        sessions = response.get_json()
        self.assertEqual([item["session_id"] for item in sessions], ["session-2", "session-1"])
        self.assertEqual(sessions[0]["session_folder"], self.session_two.name)
        self.assertEqual(sessions[0]["answers_count"], 2)
        self.assertTrue(sessions[0]["recovered"])
        self.assertNotIn("legacy-session", {item["session_id"] for item in sessions})
        self.assertTrue((self.data_dir / "study-a" / "p01" / "p01.json").is_file())
        self.assertTrue(all("/participants/" in f"/{item['session_path']}/" for item in sessions))

    def test_detail_uses_session_folder_and_returns_xdf_stream_metadata(self) -> None:
        with patch.object(sessions_index_service, "_read_merged_streams", side_effect=self._fixture_streams):
            response = self.client.get(
                "/api/admin/sessions/study-a/p01",
                query_string={"session_folder": self.session_one.name},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["result"]["answers"]["q1"], "first")
        self.assertEqual(payload["streams"][0]["stream_key"], "mr60.vitals")
        self.assertEqual(payload["streams"][0]["sample_count"], 3)
        self.assertNotIn("samples", payload["streams"][0])
        self.assertIn("derived/session.xdf", {item["name"] for item in payload["files"]})

    def test_signal_route_selects_canonical_session_and_bounds_payload(self) -> None:
        with patch.object(sessions_index_service, "_read_merged_streams", side_effect=self._fixture_streams):
            response = self.client.get(
                "/api/admin/sessions/study-a/p01/signals",
                query_string={
                    "session_folder": self.session_one.name,
                    "sensor": "mr60.vitals",
                    "max_points": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["session_folder"], self.session_one.name)
        self.assertEqual(payload["sample_count"], 3)
        self.assertEqual(payload["mode"], "min_max_envelope")
        self.assertEqual(len(payload["points"]), 2)
        self.assertLessEqual(min(point["min"]["heartRate"] for point in payload["points"]), 60)
        self.assertGreaterEqual(max(point["max"]["heartRate"] for point in payload["points"]), 90)

    def test_invalid_selectors_and_missing_sensor_are_rejected(self) -> None:
        invalid_folder = self.client.get(
            "/api/admin/sessions/study-a/p01",
            query_string={"session_folder": "../session-1"},
        )
        missing_sensor = self.client.get(
            "/api/admin/sessions/study-a/p01/signals",
            query_string={"session_folder": self.session_one.name},
        )
        invalid_limit = self.client.get(
            "/api/admin/sessions/study-a/p01/signals",
            query_string={"session_folder": self.session_one.name, "sensor": "mr60.vitals", "max_points": "unbounded"},
        )
        legacy_selector = self.client.get(
            "/api/admin/sessions/study-a/p01",
            query_string={"result_file": "p01.json"},
        )

        self.assertEqual(invalid_folder.status_code, 400)
        self.assertEqual(missing_sensor.status_code, 400)
        self.assertEqual(invalid_limit.status_code, 400)
        self.assertEqual(legacy_selector.status_code, 400)

    def test_flat_legacy_session_cannot_be_selected_through_detail_or_signals(self) -> None:
        detail = self.client.get(
            "/api/admin/sessions/study-a/p01",
            query_string={"session_id": "legacy-session"},
        )
        signals = self.client.get(
            "/api/admin/sessions/study-a/p01/signals",
            query_string={"session_id": "legacy-session", "sensor": "mr60"},
        )
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(signals.status_code, 404)

    def test_index_cache_reuses_scan_until_a_canonical_file_changes(self) -> None:
        sessions_index_service._INDEX_CACHE.clear()
        original_reader = sessions_index_service._read_json_object
        with patch.object(sessions_index_service, "_read_json_object", wraps=original_reader) as reader:
            first = list_sessions(self.data_dir)
            reads_after_first_scan = reader.call_count
            second = list_sessions(self.data_dir)

            self.assertEqual(first, second)
            self.assertGreater(reads_after_first_scan, 0)
            self.assertEqual(reader.call_count, reads_after_first_scan)

            result_file = self.session_two / "result.json"
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
        self.assertEqual(min_max_envelope(samples, 20), {"mode": "raw", "points": samples})

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

    def test_non_finite_xdf_values_are_json_safe(self) -> None:
        self.assertEqual(
            sessions_index_service._json_safe({"value": float("nan"), "nested": [float("inf"), 1.0]}),
            {"value": None, "nested": [None, 1.0]},
        )


if __name__ == "__main__":
    unittest.main()


class SignalWindowTests(unittest.TestCase):
    """Zoom asks for a slice of the recording; the slice must be honest."""

    def test_absent_window_means_the_whole_recording(self) -> None:
        self.assertIsNone(_validate_window(None, None))

    def test_both_bounds_are_required_together(self) -> None:
        with self.assertRaises(ValueError):
            _validate_window(10.0, None)
        with self.assertRaises(ValueError):
            _validate_window(None, 10.0)

    def test_an_inverted_or_empty_window_is_rejected(self) -> None:
        for start, end in ((20.0, 10.0), (10.0, 10.0)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                _validate_window(start, end)

    def test_non_finite_bounds_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _validate_window(float("nan"), 10.0)
        with self.assertRaises(ValueError):
            _validate_window(0.0, float("inf"))

    def test_a_valid_window_passes_through(self) -> None:
        self.assertEqual(_validate_window(10.0, 20.0), (10.0, 20.0))


class StreamDescriptorTests(unittest.TestCase):
    """The viewer draws from the LSL header, so it has to survive the trip."""

    def test_the_header_reaches_the_client(self) -> None:
        descriptor = _stream_descriptor({
            "name": "BrainBit EEG",
            "nominal_rate_hz": 250.0,
            "channels": ["T3", "T4"],
            "channel_types": {"T3": "EEG", "T4": "EEG"},
            "channel_units": {"T3": "microvolts"},
        })

        self.assertEqual(descriptor["stream_name"], "BrainBit EEG")
        self.assertEqual(descriptor["nominal_rate_hz"], 250.0)
        self.assertEqual(descriptor["channels"], ["T3", "T4"])
        self.assertEqual(descriptor["channel_types"]["T4"], "EEG")
        self.assertEqual(descriptor["channel_units"]["T3"], "microvolts")

    def test_a_stream_without_metadata_still_yields_a_descriptor(self) -> None:
        descriptor = _stream_descriptor({})

        self.assertEqual(descriptor["stream_name"], "")
        self.assertEqual(descriptor["channels"], [])
        self.assertEqual(descriptor["channel_types"], {})
