"""The shipped demo session must stay listable and openable.

`software/saved_results/Demo_Completed_Study/` is the real session recorded
with 0.7.0. Its only change since then is the move of `result.json` into
`answers/` when the session folder was restructured (answers/meta/raw/derived),
so it is exactly what a fresh installation shows as its example result. It
still lacks every artifact added later (`quality.jsonl`, lifecycle fields, the
meta/ folder), which proves that reading a session does not require them.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.studies.sessions_index_service import list_sessions, load_session

SAVED_RESULTS = PROJECT_ROOT / "saved_results"
STUDY_ID = "Demo Completed Study"
PARTICIPANT_ID = "ac89c1703e034cfb"
SESSION_FOLDER = "20260811T163356Z__study-session-02ec4b00debe496680afb8cffed52dea"


class Real070SessionCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        session_root = SAVED_RESULTS / "Demo_Completed_Study" / "participants" / PARTICIPANT_ID / "sessions" / SESSION_FOLDER
        self.assertTrue(
            session_root.is_dir(),
            f"{session_root} is missing -- it is a committed 0.7.0 fixture, not generated",
        )

    def test_the_session_browser_indexes_it(self) -> None:
        sessions = list_sessions(SAVED_RESULTS)
        matches = [s for s in sessions if s.get("study_id") == STUDY_ID and s.get("participant_id") == PARTICIPANT_ID]
        self.assertEqual(len(matches), 1, f"expected exactly one match, found {matches}")
        self.assertEqual(matches[0]["session_folder"], SESSION_FOLDER)

    def test_the_detail_view_opens_it_without_any_post_0_7_0_artifact(self) -> None:
        detail = load_session(SAVED_RESULTS, STUDY_ID, PARTICIPANT_ID, session_folder=SESSION_FOLDER)

        # The real, native XDF this session recorded in 0.7.0 still parses.
        self.assertEqual(len(detail["streams"]), 3)
        self.assertEqual(detail["result"]["study_id"], STUDY_ID)

        # quality.jsonl (Phase 5c) did not exist in 0.7.0 -- "not measured",
        # never a false "clean" or a crash.
        self.assertEqual(detail["quality_summary"]["recording_health"], "unknown")
        self.assertEqual(detail["quality_summary"]["findings"], [])

        # The lifecycle field (Phase 5a) is derived on read, not stored --
        # this session has none of the documents that field is derived from
        # (they postdate 0.7.0) and still resolves to a real state, not a
        # crash or an empty string.
        self.assertEqual(detail.get("lifecycle"), "SEALED")


if __name__ == "__main__":
    unittest.main()
