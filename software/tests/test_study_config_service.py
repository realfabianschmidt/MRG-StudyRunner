from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.studies import study_config_service


class StudyConfigPersistenceTests(unittest.TestCase):
    def test_config_save_is_atomic_and_leaves_no_shared_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings" / "study_config.json"
            study_config_service.save_config(path, {"study_id": "Atomic"})

            payload = json.loads(path.read_text(encoding="utf-8"))
            leftovers = [candidate for candidate in path.parent.iterdir() if candidate != path]

        self.assertEqual(payload["study_id"], "Atomic")
        self.assertEqual(leftovers, [])

    def test_active_pair_archives_new_study_before_replacing_current_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "settings" / "study_config.json"
            studies = root / "studies"
            study_config_service.save_config(current, {"study_id": "Old"})
            real_save = study_config_service.save_config

            def fail_current(path, payload):
                if Path(path) == current:
                    raise OSError("simulated current projection failure")
                return real_save(path, payload)

            with patch.object(study_config_service, "save_config", side_effect=fail_current):
                with self.assertRaises(OSError):
                    study_config_service.save_active_study(
                        current,
                        studies,
                        {"study_id": "New"},
                    )

            archived = json.loads((studies / "New.study-runner").read_text(encoding="utf-8"))
            active = json.loads(current.read_text(encoding="utf-8"))

        self.assertEqual(archived["study_id"], "New")
        self.assertEqual(active["study_id"], "Old")

    def test_interrupted_pair_is_recovered_from_revision_checked_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "settings" / "study_config.json"
            studies = root / "studies"
            study_config_service.save_config(current, {"study_id": "Old", "questions": []})
            real_save = study_config_service.save_config

            def fail_current(path, payload):
                if Path(path) == current:
                    raise OSError("simulated current projection failure")
                return real_save(path, payload)

            with patch.object(study_config_service, "save_config", side_effect=fail_current):
                with self.assertRaises(OSError):
                    study_config_service.save_active_study(
                        current,
                        studies,
                        {"study_id": "New", "questions": []},
                    )

            marker_path = study_config_service.study_transaction_path(current)
            self.assertEqual(
                json.loads(marker_path.read_text(encoding="utf-8"))["status"],
                "archive_written",
            )
            recovered = study_config_service.load_config(current)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))

        self.assertEqual(recovered["study_id"], "New")
        self.assertEqual(marker["status"], "recovered")

    def test_stale_editor_revision_is_rejected_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "settings" / "study_config.json"
            studies = root / "studies"
            original = {"study_id": "Study", "questions": [{"type": "text", "title": "old"}]}
            study_config_service.save_config(current, original)
            stale_revision = study_config_service.study_config_revision(original)
            newer = {"study_id": "Study", "questions": [{"type": "text", "title": "new"}]}
            study_config_service.save_active_study(
                current,
                studies,
                newer,
                expected_revision=stale_revision,
            )

            with self.assertRaises(study_config_service.StudyRevisionConflict):
                study_config_service.save_active_study(
                    current,
                    studies,
                    {"study_id": "Study", "questions": []},
                    expected_revision=stale_revision,
                )
            stored = study_config_service.load_config(current)

        self.assertEqual(stored["study_id"], newer["study_id"])
        self.assertEqual(stored["questions"], newer["questions"])

    def test_successful_pair_keeps_a_committed_transaction_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "settings" / "study_config.json"
            revision = study_config_service.save_active_study(
                current,
                root / "studies",
                {"study_id": "Study", "questions": []},
            )
            marker = json.loads(
                study_config_service.study_transaction_path(current).read_text(encoding="utf-8")
            )

        self.assertEqual(marker["status"], "committed")
        self.assertEqual(marker["revision"], revision)


if __name__ == "__main__":
    unittest.main()
