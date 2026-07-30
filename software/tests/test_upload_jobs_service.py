"""Persistent upload jobs: replay, backoff, migration, and route contracts."""
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
from study_runner.backend.services.folder_open_service import (
    FolderOpenError,
    resolve_results_folder,
)
from study_runner.backend.services.upload_jobs_service import (
    MAX_RETRY_AGE_SECONDS,
    UploadJobService,
    retry_delay_seconds,
)


class ManualClock:
    def __init__(self, value: float = 1_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def job_arguments() -> dict:
    return {
        "kind": "notion",
        "study_id": "study",
        "participant_id": "p01",
        "session_id": "session-1",
        "label": "Notion",
        "payload": {
            "result_payload": {"study_id": "study", "participant_id": "p01"},
            "saved_output": {"json_file": "saved_results/study/p01/p01.json"},
            "config_data": {"study_settings": {"notion_enabled": True}},
        },
        "metadata": {"answer_count": 2, "recorded_files": ["p01.json"]},
    }


class UploadJobServiceTests(unittest.TestCase):
    def test_backoff_schedule_matches_roadmap(self) -> None:
        self.assertEqual(
            [retry_delay_seconds(attempt) for attempt in range(1, 7)],
            [30, 120, 600, 1800, 3600, 3600],
        )

    def test_failed_attempt_replays_and_succeeds_when_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = ManualClock()

            def unavailable(_payload):
                raise RuntimeError("network unavailable")

            first = UploadJobService(Path(temp_dir), executors={"notion": unavailable}, clock=clock)
            job = first.enqueue(**job_arguments())
            self.assertEqual(first.process_due_jobs_once(), 1)
            first_state = first.status()["sessions"][0]["jobs"][0]
            self.assertEqual(first_state["status"], "queued")
            self.assertEqual(first_state["attempts"], 1)
            self.assertEqual(first_state["next_attempt_at"], "1970-01-12T13:47:10Z")
            self.assertIn("network unavailable", first_state["last_error"])

            completed_payloads = []
            replayed = UploadJobService(
                Path(temp_dir),
                executors={"notion": lambda payload: completed_payloads.append(payload) or {"ok": True}},
                clock=clock,
            )
            self.assertEqual(replayed.process_due_jobs_once(), 0)
            clock.value += 30
            self.assertEqual(replayed.process_due_jobs_once(), 1)
            final = replayed.status()["sessions"][0]["jobs"][0]
            self.assertEqual(final["job_id"], job["job_id"])
            self.assertEqual(final["status"], "done")
            self.assertEqual(final["attempts"], 2)
            self.assertEqual(len(completed_payloads), 1)
            self.assertFalse((Path(temp_dir) / "upload_jobs" / f"{job['job_id']}.json").exists())

    def test_running_job_is_requeued_during_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = ManualClock()
            service = UploadJobService(Path(temp_dir), clock=clock)
            job = service.enqueue(**job_arguments())
            event = {"event": "attempt", "job_id": job["job_id"], "attempts": 1}
            service._append_event(event)
            service._apply_event(event)

            replayed = UploadJobService(Path(temp_dir), clock=clock)
            restored = replayed.status()["sessions"][0]["jobs"][0]
            self.assertEqual(restored["status"], "queued")
            self.assertIn("Server restarted", restored["last_error"])
            self.assertEqual(restored["next_attempt_at"], "1970-01-12T13:46:40Z")

    def test_automatic_retries_stop_after_48_hours(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = ManualClock()
            service = UploadJobService(
                Path(temp_dir),
                executors={"notion": lambda _payload: (_ for _ in ()).throw(RuntimeError("still offline"))},
                clock=clock,
            )
            service.enqueue(**job_arguments())
            clock.value += MAX_RETRY_AGE_SECONDS
            service.process_due_jobs_once()

            job = service.status()["sessions"][0]["jobs"][0]
            self.assertEqual(job["status"], "failed")
            self.assertIn("still offline", job["last_error"])

    def test_legacy_queue_migration_is_idempotent_and_preserves_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "notion_upload_queue.jsonl"
            entry = {
                **job_arguments()["payload"],
                "hardware_config": {
                    "notion": {"api_key": "legacy-secret"},
                    "nextcloud": {"password": "legacy-share-secret"},
                },
                "queued_at": "2026-07-01T10:00:00Z",
            }
            legacy.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            service = UploadJobService(root)

            first = service.migrate_legacy_notion_queue()
            self.assertEqual(first, {"found": 1, "migrated": 1})
            self.assertFalse(legacy.exists())
            first_job_id = service.status(days=90)["sessions"][0]["jobs"][0]["job_id"]
            migrated_payload = (root / "upload_jobs" / f"{first_job_id}.json").read_text(encoding="utf-8")
            self.assertNotIn("legacy-secret", migrated_payload)
            self.assertNotIn("legacy-share-secret", migrated_payload)

            legacy.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            second = service.migrate_legacy_notion_queue()
            self.assertEqual(second, {"found": 1, "migrated": 1})
            jobs = service.status(days=90)["sessions"][0]["jobs"]
            self.assertEqual([job["job_id"] for job in jobs], [first_job_id])

            legacy.write_text("[]\n", encoding="utf-8")
            invalid = service.migrate_legacy_notion_queue()
            self.assertIn("invalid entry", invalid["error"])
            self.assertTrue(legacy.exists())

    def test_public_status_never_contains_private_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = UploadJobService(Path(temp_dir))
            service.enqueue(
                **{
                    **job_arguments(),
                    "payload": {
                        **job_arguments()["payload"],
                        "hardware_config": {"notion": {"api_key": "must-not-leak"}},
                    },
                }
            )

            response = json.dumps(service.status())
            self.assertNotIn("must-not-leak", response)
            self.assertNotIn("hardware_config", response)


class FolderOpenServiceTests(unittest.TestCase):
    def test_folder_resolution_is_bounded_to_results_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = root / "study" / "p01"
            expected.mkdir(parents=True)
            self.assertEqual(resolve_results_folder(root, "study", "p01"), expected.resolve())
            with self.assertRaises(FolderOpenError):
                resolve_results_folder(root, "../study", "p01")


class UploadRoutesTests(unittest.TestCase):
    def _app(self, data_dir: str):
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

    def test_status_and_manual_retry_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            service = app.config["UPLOAD_JOBS_SERVICE"]
            job = service.enqueue(**job_arguments())
            client = app.test_client()

            status = client.get("/api/uploads/status?days=7")
            retry = client.post("/api/uploads/retry", json={"job_id": job["job_id"]})
            invalid = client.post("/api/uploads/retry", json={})

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["sessions"][0]["jobs"][0]["job_id"], job["job_id"])
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()["retried"], 1)
        self.assertEqual(invalid.status_code, 400)

    def test_open_folder_route_uses_cross_platform_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = self._app(temp_dir)
            with patch(
                "study_runner.backend.routes.uploads.open_results_folder",
                return_value={"ok": True, "path": "/results/study/p01"},
            ) as opener:
                response = app.test_client().post(
                    "/api/admin/system/open-results-folder",
                    json={"study_id": "study", "participant_id": "p01"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        opener.assert_called_once_with(app.config["DATA_DIR"], "study", "p01")


if __name__ == "__main__":
    unittest.main()
