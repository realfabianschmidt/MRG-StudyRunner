"""Flask-level coverage for the recovery routes (candidate list, finalize, discard)."""
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
from study_runner.backend.services.shared.atomic_io import atomic_write_json


def _app(data_dir: str):
    with patch.dict(
        os.environ,
        {
            "STUDY_RUNNER_DATA_DIR": data_dir,
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        },
        clear=False,
    ):
        return create_app()


def _load_a_study(client) -> None:
    response = client.post(
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
    assert response.status_code == 200, response.get_json()


def _write_partial(data_dir: Path, session_id: str) -> None:
    atomic_write_json(
        data_dir / "study-a" / "_partial" / f"{session_id}.json",
        {
            "session_id": session_id,
            "study_id": "study-a",
            "participant_id": "hashabc123",
            "timestamp_start": "2026-01-01T10:00:00Z",
            "snapshot_at": "2026-01-01T10:05:00Z",
            "current_index": 1,
            "answers": {"q1": "great"},
            "participant_metadata": {},
            "answer_events": [],
            "card_events": [],
        },
    )


class RecoveryRoutesTests(unittest.TestCase):
    def test_candidates_route_lists_a_partial_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            _load_a_study(client)
            _write_partial(app.config["DATA_DIR"], "session-1")

            response = client.get("/api/admin/recovery")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["candidates"]), 1)
        self.assertEqual(body["candidates"][0]["recovery_id"], "partial:study-a:session-1")

    def test_candidates_route_hides_a_still_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            _load_a_study(client)
            start = client.post(
                "/api/study/session/start",
                json={"participant_id": "hash1234", "current_index": 0, "current_type": "participant-id"},
            )
            session_id = start.get_json()["session"]["session_id"]
            _write_partial(app.config["DATA_DIR"], session_id)

            response = client.get("/api/admin/recovery")

        self.assertEqual(response.get_json()["candidates"], [])

    def test_candidates_route_lists_a_finish_session_stuck_in_active_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            _load_a_study(client)
            start = client.post(
                "/api/study/session/start",
                json={"participant_id": "hash1234", "current_index": 2, "current_type": "finish"},
            )
            session_id = start.get_json()["session"]["session_id"]
            _write_partial(app.config["DATA_DIR"], session_id)
            store = app.config["SESSION_STORE"]
            store._sessions[session_id]["current_type"] = "finish"
            store._sessions[session_id]["last_seen"] = 0.0
            store._persist()

            response = client.get("/api/admin/recovery")

        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(body["candidates"]), 1)
        self.assertTrue(body["candidates"][0]["stuck_active"])

    def test_finalize_route_saves_a_result_and_returns_its_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            _load_a_study(client)
            _write_partial(app.config["DATA_DIR"], "session-1")

            response = client.post("/api/admin/recovery/finalize", json={"recovery_id": "partial:study-a:session-1"})

            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertTrue(body["ok"])
            self.assertTrue(Path(temp_dir, body["json_file"]).is_file())

    def test_finalize_twice_returns_not_found_the_second_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            _load_a_study(client)
            _write_partial(app.config["DATA_DIR"], "session-1")

            first = client.post("/api/admin/recovery/finalize", json={"recovery_id": "partial:study-a:session-1"})
            second = client.post("/api/admin/recovery/finalize", json={"recovery_id": "partial:study-a:session-1"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)

    def test_finalize_without_recovery_id_is_a_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            response = app.test_client().post("/api/admin/recovery/finalize", json={})

        self.assertEqual(response.status_code, 400)

    def test_discard_route_archives_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            client = app.test_client()
            _load_a_study(client)
            _write_partial(app.config["DATA_DIR"], "session-1")

            response = client.post("/api/admin/recovery/discard", json={"recovery_id": "partial:study-a:session-1"})
            after = client.get("/api/admin/recovery")

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["ok"])
            self.assertEqual(after.get_json()["candidates"], [])
            self.assertTrue(
                Path(app.config["DATA_DIR"], "study-a", "_recovery", "discarded", "session-1.json").is_file()
            )

    def test_discard_unknown_recovery_id_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = _app(temp_dir)
            response = app.test_client().post(
                "/api/admin/recovery/discard", json={"recovery_id": "partial:study-a:missing"}
            )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
