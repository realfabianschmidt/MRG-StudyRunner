"""Removing the old acquisition plugin must not hide historical sessions."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.studies.sessions_index_service import list_sessions, load_session


FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "bb_old"


class BrainBitOldSessionCompatibilityTests(unittest.TestCase):
    def test_historical_brainbit_old_session_remains_browsable_without_the_plugin(self) -> None:
        sessions = list_sessions(FIXTURE_ROOT)
        self.assertEqual(len(sessions), 1)
        summary = sessions[0]
        self.assertEqual(summary["study_id"], "Legacy BrainBit Study")
        self.assertEqual(summary["participant_id"], "p1")

        detail = load_session(
            FIXTURE_ROOT,
            "Legacy BrainBit Study",
            "p1",
            session_folder=summary["session_folder"],
        )
        recorded = detail["result"]["recorded_plugins"]["brainbit_old"]
        self.assertEqual(recorded["stream_source_id"], "study_runner.brainbit_old.eeg")
        self.assertNotIn("brainbit", detail["result"]["recorded_plugins"])


if __name__ == "__main__":
    unittest.main()
