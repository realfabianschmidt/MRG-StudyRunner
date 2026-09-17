"""Phase 6.3: a real 0.7.0 session must still open today, unmodified.

`software/saved_results/Demo_Completed_Study/` is not a synthetic fixture --
it is the exact session folder shipped in tag `app-v0.7.0` (verified with
`git diff app-v0.7.0 -- software/saved_results/Demo_Completed_Study`: no
difference at all). Every artifact the 1.0 rebuild's Phase 5 packages added
(`quality.jsonl` for 5c, the derived `lifecycle` field for 5a, ...) postdates
this session, so it proves the real thing 6.3 asks for: reading an old
session must not require artifacts that did not exist when it was recorded.

This complements test_sessions_routes.py's synthetic per-field fixtures
(which pin the same "optional on read" behavior with hand-built data); this
file pins it against bytes nobody manufactured for a test.
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


@unittest.skip(
    "v4 layout (meta/, answers/) deliberately drops read-compatibility for "
    "sessions recorded before this reorg -- decided 2026-09-17, no real "
    "pre-v4 session data exists yet and the coming study records directly "
    "in the new shape. This fixture and the guarantee it pinned are kept "
    "here, skipped rather than deleted, in case that decision is revisited."
)
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
