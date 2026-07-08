from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.runtime_config import (
    get_app_mode,
    get_server_scheme,
    initialize_runtime_storage,
    is_https_enabled,
    resolve_runtime_paths,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_frozen_runtime_defaults_to_packaged_mode(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "frozen", True, create=True):
            self.assertEqual(get_app_mode(), "packaged")

    def test_https_is_enabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(is_https_enabled())
            self.assertEqual(get_server_scheme(), "https")

    def test_https_can_be_explicitly_disabled(self) -> None:
        with patch.dict(os.environ, {"STUDY_RUNNER_HTTPS": "0"}, clear=True):
            self.assertFalse(is_https_enabled())
            self.assertEqual(get_server_scheme(), "http")

    def test_default_paths_stay_inside_project(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            paths = resolve_runtime_paths(PROJECT_ROOT)

        self.assertFalse(paths.uses_external_storage)
        self.assertEqual(paths.content_dir, PROJECT_ROOT / "study_content")
        self.assertEqual(paths.settings_dir, PROJECT_ROOT / "study_content" / "settings")
        self.assertEqual(paths.data_dir, PROJECT_ROOT / "saved_results")
        self.assertEqual(paths.saved_studies_dir, PROJECT_ROOT / "study_content" / "studies")

    def test_external_data_dir_is_seeded_from_project_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as data_dir:
            base = Path(base_dir)
            (base / "study_content" / "settings").mkdir(parents=True)
            (base / "study_content" / "settings" / "study_config.json").write_text('{"study_id": "Default"}', encoding="utf-8")
            (base / "study_content" / "settings" / "hardware_settings.json").write_text('{"lsl": {"enabled": false}}', encoding="utf-8")
            (base / "study_content" / "studies").mkdir()
            (base / "study_content" / "studies" / "Default.study-runner").write_text('{"study_id": "Default"}', encoding="utf-8")

            with patch.dict(os.environ, {"STUDY_RUNNER_DATA_DIR": data_dir}, clear=True):
                paths = resolve_runtime_paths(base)
                initialize_runtime_storage(paths)

            storage = Path(data_dir)
            self.assertTrue((storage / "settings" / "study_config.json").exists())
            self.assertTrue((storage / "settings" / "hardware_settings.json").exists())
            self.assertTrue((storage / "studies" / "Default.study-runner").exists())
            self.assertTrue((storage / "saved_results").is_dir())


if __name__ == "__main__":
    unittest.main()
