from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.data_core.host.artifacts import ArtifactStore, SessionIdentity


class RecordingArtifactTests(unittest.TestCase):
    def test_reserves_study_participant_session_layout_idempotently(self) -> None:
        identity = SessionIdentity(
            study_id="Study A",
            participant_id="P/01",
            session_id="6a4d-example",
            started_at=dt.datetime(2026, 7, 31, 18, 2, 3, tzinfo=dt.timezone.utc),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            first = store.reserve(identity)
            second = store.reserve(identity)

            self.assertEqual(first.root, second.root)
            self.assertEqual(first.root.parent.name, first.identity.participant_component)
            self.assertEqual(first.root.parent.parent.name, first.identity.study_component)
            self.assertNotIn("participants", first.root.parts)
            self.assertTrue(first.root.name.startswith("20260731T180203Z__"))
            self.assertNotIn("/", first.identity.participant_component)
            payload = json.loads(first.identity_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["participant_id"], "P/01")
            self.assertTrue(first.raw_plugins_dir.is_dir())
            self.assertEqual(first.merged_xdf.name, "session.xdf")
            self.assertEqual(first.csv_export_file.name, "session_1hz.csv")

    def test_normalized_identifiers_keep_collision_digest(self) -> None:
        started = dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)
        first = SessionIdentity("study", "a/b", "one", started)
        second = SessionIdentity("study", "a?b", "one", started)

        self.assertNotEqual(first.participant_component, second.participant_component)

    def test_case_variants_and_windows_devices_never_share_a_component(self) -> None:
        started = dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)
        lower = SessionIdentity("study", "p01", "one", started)
        upper = SessionIdentity("study", "P01", "one", started)
        reserved = SessionIdentity("study", "CON.txt", "one", started)

        self.assertNotEqual(lower.participant_component.casefold(), upper.participant_component.casefold())
        self.assertNotEqual(reserved.participant_component.casefold(), "con.txt")

    def test_internal_folder_names_cannot_be_used_as_study_or_participant_components(self) -> None:
        started = dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc)
        self.assertNotEqual(SessionIdentity("runtime", "p01", "one", started).study_component, "runtime")
        self.assertNotEqual(SessionIdentity("upload_jobs", "p01", "one", started).study_component, "upload_jobs")
        self.assertNotEqual(SessionIdentity("study", "_work", "one", started).participant_component, "_work")


if __name__ == "__main__":
    unittest.main()
