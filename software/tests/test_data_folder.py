"""The operator-chosen data folder (runtime_core/settings/data_folder.py).

Studies, results, settings, credentials and the certificate can live in one
folder outside the program: chosen in the settings, kept across updates,
relinked after a reinstall. A folder that is not reachable stops the start
instead of silently creating an empty one.
"""
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

from study_runner.runtime_core.settings import data_folder
from study_runner.runtime_core.settings.runtime_config import initialize_runtime_storage, resolve_runtime_paths


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.install = self.base / "install"
        self.install.mkdir()

    def _data_folder(self, name: str = "data") -> Path:
        folder = self.base / name
        folder.mkdir()
        data_folder.write_marker(folder, "9.9.9")
        return folder

    def test_without_a_setting_the_program_folder_is_used(self) -> None:
        env: dict[str, str] = {}
        self.assertIsNone(data_folder.apply_data_folder_setting(self.install, env))
        self.assertNotIn(data_folder.ENV_DATA_DIR, env)

    def test_the_setting_becomes_the_data_dir_for_the_whole_app(self) -> None:
        folder = self._data_folder()
        data_folder.write_setting(folder, self.install)
        env: dict[str, str] = {}
        self.assertEqual(data_folder.apply_data_folder_setting(self.install, env), folder)
        self.assertEqual(env[data_folder.ENV_DATA_DIR], str(folder))
        self.assertEqual(env[data_folder.ENV_FROM_SETTING], "1")

    def test_a_hand_set_environment_variable_wins(self) -> None:
        data_folder.write_setting(self._data_folder(), self.install)
        env = {data_folder.ENV_DATA_DIR: "/ci/data"}
        self.assertEqual(data_folder.apply_data_folder_setting(self.install, env), Path("/ci/data"))
        self.assertEqual(env[data_folder.ENV_DATA_DIR], "/ci/data")

    def test_a_restarted_server_reads_the_changed_setting_not_the_inherited_folder(self) -> None:
        old, new = self._data_folder("old"), self._data_folder("new")
        env = {data_folder.ENV_DATA_DIR: str(old), data_folder.ENV_FROM_SETTING: "1"}
        data_folder.write_setting(new, self.install)
        self.assertEqual(data_folder.apply_data_folder_setting(self.install, env), new)
        data_folder.write_setting(None, self.install)  # back to the program folder
        self.assertIsNone(data_folder.apply_data_folder_setting(self.install, env))
        self.assertNotIn(data_folder.ENV_DATA_DIR, env)

    def test_an_unreachable_folder_stops_the_start_and_creates_nothing(self) -> None:
        missing = self.base / "unplugged-drive" / "StudyRunner"
        data_folder.write_setting(missing, self.install)
        with self.assertRaises(data_folder.DataFolderError) as raised:
            data_folder.apply_data_folder_setting(self.install, {})
        self.assertIn(str(missing), str(raised.exception))
        self.assertIn(data_folder.SETTING_FILE_NAME, str(raised.exception))
        self.assertFalse(missing.exists())

    def test_a_folder_set_up_before_the_marker_existed_still_counts(self) -> None:
        folder = self.base / "older"
        (folder / "settings").mkdir(parents=True)
        (folder / "saved_results").mkdir()
        self.assertTrue(data_folder.is_data_folder(folder))


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.install = self.base / "install"
        (self.install / "software").mkdir(parents=True)

    def test_empty_or_missing_folders_are_new_and_data_folders_are_linked(self) -> None:
        self.assertEqual(data_folder.check_candidate(self.base / "fresh", self.install), "new")
        existing = self.base / "existing"
        existing.mkdir()
        data_folder.write_marker(existing, "1.0.0")
        self.assertEqual(data_folder.check_candidate(existing, self.install), "link")

    def test_unusable_folders_are_refused_with_a_reason(self) -> None:
        cluttered = self.base / "documents"
        cluttered.mkdir()
        (cluttered / "letter.txt").write_text("x", encoding="utf-8")
        cases = {
            "": "Enter or choose",
            "relative/folder": "full path",
            str(self.install / "software" / "data"): "outside the Study Runner program folder",
            str(cluttered): "already contains other files",
        }
        for path, reason in cases.items():
            with self.subTest(path=path):
                with self.assertRaisesRegex(data_folder.DataFolderError, reason):
                    data_folder.check_candidate(path, self.install)


class PrepareTests(unittest.TestCase):
    def test_a_new_empty_folder_is_set_up_like_a_clean_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            program = base / "software"
            (program / "study_content" / "settings").mkdir(parents=True)
            (program / "study_content" / "settings" / "study_config.json").write_text("{}", encoding="utf-8")
            (program / "study_content" / "studies").mkdir()
            (program / "study_content" / "studies" / "Example.study-runner").write_text("x", encoding="utf-8")
            target = base / "external"
            data_folder.prepare_new_folder(target, None, "9.9.9")
            with patch.dict(os.environ, {"STUDY_RUNNER_DATA_DIR": str(target)}, clear=True):
                initialize_runtime_storage(resolve_runtime_paths(program))
            self.assertTrue(data_folder.is_data_folder(target))
            self.assertTrue((target / "settings" / "study_config.json").is_file())
            self.assertTrue((target / "studies" / "Example.study-runner").is_file())
            self.assertTrue((target / "saved_results").is_dir())

    def test_bringing_the_current_data_along_copies_everything_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            program = base / "software"
            with patch.dict(os.environ, {}, clear=True):
                paths = resolve_runtime_paths(program)
            files = {
                paths.settings_dir / "local_secrets.json": "secret",
                paths.settings_dir / "ssl" / "study-runner-local-root-ca.crt": "ca",
                paths.saved_studies_dir / "Mine.study-runner": "study",
                paths.data_dir / "Study" / "result.json": "result",
                paths.storage_root / "runtime" / "brainbit" / "logs" / "a.log": "log",
            }
            for path, text in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            config = {
                "STORAGE_ROOT": paths.storage_root, "SETTINGS_DIR": paths.settings_dir,
                "SAVED_STUDIES_DIR": paths.saved_studies_dir, "DATA_DIR": paths.data_dir,
            }
            target = base / "external"
            copied = data_folder.prepare_new_folder(target, config, "9.9.9")
            self.assertEqual(copied, ["settings", "studies", "saved_results", "runtime"])
            with patch.dict(os.environ, {"STUDY_RUNNER_DATA_DIR": str(target)}, clear=True):
                moved = resolve_runtime_paths(program)
            self.assertEqual((moved.settings_dir / "local_secrets.json").read_text(encoding="utf-8"), "secret")
            self.assertTrue((moved.settings_dir / "ssl" / "study-runner-local-root-ca.crt").is_file())
            self.assertTrue((moved.saved_studies_dir / "Mine.study-runner").is_file())
            self.assertTrue((moved.data_dir / "Study" / "result.json").is_file())
            self.assertTrue((target / "runtime" / "brainbit" / "logs" / "a.log").is_file())
            self.assertTrue((paths.data_dir / "Study" / "result.json").is_file())  # the old copy stays


class RememberedTests(unittest.TestCase):
    def test_only_reachable_folders_are_offered_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            first, second = base / "first", base / "second"
            for folder in (first, second):
                folder.mkdir()
                data_folder.write_marker(folder, "1.0.0")
            with patch.dict(os.environ, {"STUDY_RUNNER_USER_CONFIG_DIR": str(base / "user")}):
                data_folder.remember(first)
                data_folder.remember(second)
                data_folder.remember(base / "gone")
                self.assertEqual(data_folder.remembered_folders(), [str(second), str(first)])
                stored = json.loads((base / "user" / data_folder.RECENT_FILE_NAME).read_text(encoding="utf-8"))
                self.assertEqual(stored["folders"][0], str(base / "gone"))


if __name__ == "__main__":
    unittest.main()
