"""A completed 0.6-era flat result must stay dedup-visible and browser-invisible.

Target doc `MRG_Recorder_Core_Architektur_1.0.md` section 13 proposes removing
"legacy flat result folders" as part of the 1.0 cleanup. That folder shape is
not test data -- it is on disk right now, for real, on this developer's
machine (`software/saved_results/Example_Sensors_Study/<participant>/`). A
release that can no longer make sense of a researcher's earlier recordings is
not cleanup. docs/archive/architecture-1.0-umbau.md (decision D2) keeps the read path
and asks for a fixture test; this is that test.

The fixture at `fixtures/legacy_flat_result/` is a synthetic stand-in for that
shape: `<study>/<participant>/<file>.json` with `study_id`, `session_id`, and
an `answers` object, matching what
`recovery_service._saved_session_identities` actually reads.

Two behaviors are pinned, both already correct today:

1. Recovery must still recognize the identity so it never re-offers a session
   that was already saved by the old flat path as a crash-recovery candidate
   (`recovery_service.list_recovery_candidates`).
2. The canonical session browser must still ignore it -- it only ever indexes
   the v3 `<study>/participants/<participant>/sessions/<...>` layout
   (`sessions_index_service.list_sessions`).

If a future change removes the read in (1), this test fails instead of that
surfacing later as a duplicate "interrupted session" prompt for an operator
who already has the result. If a future change starts indexing (1) into the
browser, this test also fails -- that would silently promise cards/timelines
the old format never recorded.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from study_runner.runtime_core.studies.recovery_service import list_recovery_candidates
from study_runner.runtime_core.studies.sessions_index_service import list_sessions
from study_runner.shared.atomic_io import atomic_write_json


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "legacy_flat_result"
FIXTURE_STUDY = "Fixture Legacy Study"
FIXTURE_PARTICIPANT = "participant-fixture-01"
FIXTURE_SESSION_ID = "session-fixture-01"


class LegacyFlatResultCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            FIXTURE_ROOT.is_dir(),
            f"{FIXTURE_ROOT} is missing -- it is a committed fixture, not generated",
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "saved_results"
        shutil.copytree(FIXTURE_ROOT, self.data_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fixture_matches_the_real_flat_shape_still_on_disk(self) -> None:
        """Pin the fixture's own shape against what the reader actually needs."""
        result_files = list((self.data_dir / FIXTURE_STUDY / FIXTURE_PARTICIPANT).glob("*.json"))
        self.assertEqual(len(result_files), 1)
        payload = json.loads(result_files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["study_id"], FIXTURE_STUDY)
        self.assertEqual(payload["session_id"], FIXTURE_SESSION_ID)
        self.assertIsInstance(payload["answers"], dict)

    def test_recovery_does_not_reoffer_an_already_saved_flat_result(self) -> None:
        partial_path = self.data_dir / FIXTURE_STUDY / "_partial" / f"{FIXTURE_SESSION_ID}.json"
        atomic_write_json(
            partial_path,
            {
                "session_id": FIXTURE_SESSION_ID,
                "client_id": "tablet-1",
                "study_id": FIXTURE_STUDY,
                "participant_id": FIXTURE_PARTICIPANT,
                "client_clock_offset_ms": 0,
                "timestamp_start": "2025-01-01T10:00:00Z",
                "snapshot_at": "2025-01-01T10:05:00Z",
                "current_index": 1,
                "answers": {"q1": "synthetic-answer"},
                "participant_metadata": {},
                "answer_events": [],
                "card_events": [],
            },
        )

        candidates = list_recovery_candidates(self.data_dir, hardware_config={})

        session_ids = {candidate.get("session_id") for candidate in candidates}
        self.assertNotIn(
            FIXTURE_SESSION_ID,
            session_ids,
            "a session already saved by the legacy flat path must not resurface "
            "as an unfinished-recording recovery candidate",
        )

    def test_canonical_browser_ignores_the_flat_result(self) -> None:
        sessions = list_sessions(self.data_dir)
        session_ids = {session.get("session_id") for session in sessions}
        self.assertNotIn(
            FIXTURE_SESSION_ID,
            session_ids,
            "the v3 session browser must only ever index the canonical "
            "participants/<id>/sessions/<...> layout",
        )


if __name__ == "__main__":
    unittest.main()
