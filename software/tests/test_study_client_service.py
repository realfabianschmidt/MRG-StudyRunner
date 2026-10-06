"""Participant connection eligibility and operator-facing device codes."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from study_runner.runtime_core.studies import study_client_service as service


class StudyClientServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        service.reset_client_status()
        self.addCleanup(service.reset_client_status)

    def test_display_code_is_stable_and_stale_page_cannot_be_selected(self) -> None:
        payload = {"client_id": "tablet-1", "study_id": "study-a", "waiting_for_admin_start": True}
        with patch.object(service.time, "time", return_value=1000.0):
            first = service.register_heartbeat(payload, "127.0.0.1", "test")
        with patch.object(service.time, "time", return_value=1002.0):
            second = service.register_heartbeat(payload, "127.0.0.1", "test")
        self.assertEqual(first["display_id"], second["display_id"])
        with patch.object(service.time, "time", return_value=1013.0):
            self.assertFalse(service.eligible_client("tablet-1", "study-a"))
            self.assertEqual(service.get_client_status("study-a")["clients"][0]["status"], "stale")
        with patch.object(service.time, "time", return_value=1093.0):
            self.assertEqual(service.get_client_status("study-a")["clients"], [])

    def test_loading_page_is_not_an_eligible_target(self) -> None:
        service.register_heartbeat({"client_id": "loading", "study_id": "", "waiting_for_admin_start": False}, None, None)
        self.assertFalse(service.eligible_client("loading", "study-a"))


if __name__ == "__main__":
    unittest.main()
