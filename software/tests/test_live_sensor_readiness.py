"""Play warns when a sensor the study selected is not delivering data."""
from __future__ import annotations

import unittest

from study_runner.runtime_core.studies.live_sensor_readiness import live_sensor_issues


MANIFESTS = {
    "eeg": {"capabilities": {"study_sensor": {}}, "ui": {"label": "EEG headband"}},
    "radar": {"capabilities": {"study_sensor": {}}, "ui": {"label": "Radar"}},
    "notion": {"capabilities": {"upload_destination": {}}},
}


def _config(**selections):
    return {"study_settings": {"plugins": selections}}


class LiveSensorReadinessTests(unittest.TestCase):
    def test_only_selected_sensors_that_are_not_live_are_reported(self) -> None:
        issues = live_sensor_issues(
            _config(eeg={"enabled": True}, radar={"enabled": True}, notion={"enabled": True}),
            {
                "eeg": {"status": "waiting", "last_message": "Headband not found."},
                "radar": {"status": "connected"},
            },
            MANIFESTS,
        )
        self.assertEqual(
            issues,
            [{"plugin": "eeg", "label": "EEG headband", "status": "waiting", "problem": "Headband not found."}],
        )

    def test_a_disabled_selection_or_missing_status_is_handled(self) -> None:
        issues = live_sensor_issues(_config(eeg={"enabled": False}, radar={"enabled": True}), {}, MANIFESTS)
        self.assertEqual([issue["plugin"] for issue in issues], ["radar"])
        self.assertEqual(issues[0]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
