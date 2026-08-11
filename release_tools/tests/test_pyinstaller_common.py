from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from release_tools.pyinstaller import study_runner_server_common as common


class PyInstallerCommonTests(unittest.TestCase):
    def test_empty_plugin_tree_keeps_frontend_at_runtime_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "study_runner" / "frontend").mkdir(parents=True)
            (root / "study_runner" / "plugins").mkdir()
            (root / "study_content").mkdir()

            with mock.patch.object(common, "collect_data_files") as collect_data:
                datas = common.common_datas(root)

            self.assertIn(
                (str(root / "study_runner" / "frontend"), "study_runner/frontend"),
                datas,
            )
            self.assertNotIn("study_runner/web", {destination for _source, destination in datas})
            collect_data.assert_not_called()

            with mock.patch.object(
                common,
                "collect_dynamic_libs",
                side_effect=AssertionError("empty plugin builds must not collect BrainBit SDKs"),
            ):
                self.assertEqual(common.common_binaries(root), [])

            with mock.patch.object(
                common,
                "collect_submodules",
                side_effect=lambda package: [package],
            ):
                hidden = common.common_hidden_imports(root)
            self.assertIn("study_runner.backend", hidden)
            self.assertNotIn("study_runner.plugins.brainbit.brainbit_realtime_cli", hidden)
            self.assertNotIn("pythonosc", hidden)
            self.assertNotIn("deepface", hidden)

    def test_camera_assets_are_required_only_when_camera_plugin_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "study_runner" / "frontend").mkdir(parents=True)
            plugin = root / "study_runner" / "plugins" / "renamed_camera_folder"
            plugin.mkdir(parents=True)
            (root / "study_content").mkdir()
            (plugin / "manifest.json").write_text(
                json.dumps({"plugin_key": "camera_emotion", "ui": {}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "DeepFace model weights are missing"):
                common.common_datas(root)


if __name__ == "__main__":
    unittest.main()
