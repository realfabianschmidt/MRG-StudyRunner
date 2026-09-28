"""Every upload destination offers the same settings on this computer.

Each plugin keeps its own values (Nextcloud may wait longer than Notion), but
all of them declare `enabled`, `timeout_seconds`, `auto_retry` and
`retry_hours`, and the upload queue honours the retry settings per plugin.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from study_runner.plugin_framework.registry import get_plugin_manifests
from study_runner.runtime_core.delivery.upload_jobs_service import UploadJobService
from study_runner.runtime_core.studies.study_readiness_service import check_study_readiness


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT.parent / "tools" / "plugin_templates" / "destinations" / "manifest.json"
STANDARD_FIELDS = {"enabled", "timeout_seconds", "auto_retry", "retry_hours"}


class DestinationSettingsContractTests(unittest.TestCase):
    def test_every_upload_destination_declares_the_standard_fields(self) -> None:
        destinations = {
            key: manifest for key, manifest in get_plugin_manifests().items()
            if "upload_destination" in (manifest.get("capabilities") or {})
        }
        self.assertGreaterEqual(len(destinations), 2)
        for key, manifest in destinations.items():
            with self.subTest(plugin=key):
                machine = (manifest.get("settings") or {}).get("machine") or {}
                self.assertTrue(STANDARD_FIELDS <= set(machine), sorted(STANDARD_FIELDS - set(machine)))
                self.assertTrue((manifest.get("capability_config") or {}).get("readiness_requirements", {}).get("requires_machine_enabled"))

    def test_the_sdk_template_starts_with_the_standard_fields(self) -> None:
        machine = json.loads(TEMPLATE.read_text(encoding="utf-8"))["settings"]["machine"]
        self.assertTrue(STANDARD_FIELDS <= set(machine))


class DestinationRetryPolicyTests(unittest.TestCase):
    def _service(self, directory: str, settings: dict, clock) -> UploadJobService:
        service = UploadJobService(Path(directory), clock=clock)
        service.register_executor(
            "example",
            lambda payload: (_ for _ in ()).throw(RuntimeError("offline")),
            retry_policy=lambda: settings,
        )
        return service

    def _enqueue(self, service: UploadJobService) -> str:
        job = service.enqueue(kind="example", label="Example", study_id="s", participant_id="p", session_id="x", payload={})
        return job["job_id"]

    def test_auto_retry_off_waits_for_a_manual_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(directory, {"auto_retry": False, "retry_hours": 48}, lambda: 1000.0)
            job_id = self._enqueue(service)
            service.process_due_jobs_once()
            jobs = service.status()["sessions"][0]["jobs"]
            self.assertEqual(next(j for j in jobs if j["job_id"] == job_id)["status"], "failed")

    def test_retry_hours_ends_retrying_per_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = [1000.0]
            service = self._service(directory, {"auto_retry": True, "retry_hours": 1}, lambda: now[0])
            job_id = self._enqueue(service)
            service.process_due_jobs_once()
            job = next(j for j in service.status()["sessions"][0]["jobs"] if j["job_id"] == job_id)
            self.assertNotEqual(job["status"], "failed", "retries within the window")
            now[0] += 2 * 3600
            service.retry(job_id=job_id)
            service.process_due_jobs_once()
            job = next(j for j in service.status()["sessions"][0]["jobs"] if j["job_id"] == job_id)
            self.assertEqual(job["status"], "failed", "past retry_hours the job stops retrying")


class DestinationMachineSwitchTests(unittest.TestCase):
    def test_a_destination_switched_off_on_this_computer_is_reported(self) -> None:
        config = {
            "study_id": "Study",
            "questions": [],
            "study_settings": {"plugins": {"nextcloud": {"enabled": True, "settings": {"share_link": "https://cloud.example/s/token"}}}},
        }
        readiness = check_study_readiness(config, {"nextcloud": {"enabled": False}}, {})
        codes = [blocker.get("code") for blocker in readiness.get("blockers", [])]
        self.assertIn("nextcloud.machine_disabled", codes)


if __name__ == "__main__":
    unittest.main()
