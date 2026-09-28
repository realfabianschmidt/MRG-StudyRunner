"""Plugins write logs, caches and state next to the results, never into the
program files: an update replaces the program files (the old ones go to its
backup), the runtime folder stays. One helper, used by every plugin."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.plugin_api import PluginContext
from study_runner.plugin_framework.adapter_utils import plugin_runtime_dir, runtime_path_setting


def _context(data_dir: str = "/lab/software/saved_results") -> PluginContext:
    return PluginContext(
        base_dir=Path("/lab/software"),
        data_dir=Path(data_dir),
        hardware_config={},
        local_secrets={},
        local_secrets_file=Path("/lab/software/study_content/settings/local_secrets.json"),
    )


class PluginRuntimeDirTests(unittest.TestCase):
    def test_runtime_folder_sits_next_to_the_results(self) -> None:
        self.assertEqual(plugin_runtime_dir(_context(), "x", "logs"), Path("/lab/software/runtime/x/logs").resolve())
        external = _context("/data/study-runner")  # STUDY_RUNNER_DATA_DIR
        self.assertEqual(plugin_runtime_dir(external, "x"), Path("/data/study-runner/runtime/x").resolve())

    def test_empty_or_program_file_settings_become_the_runtime_folder(self) -> None:
        expected = plugin_runtime_dir(_context(), "x", "logs")
        for configured in (None, "", "study_runner/plugins/sensors/x/logs"):
            with self.subTest(configured=configured):
                self.assertEqual(runtime_path_setting(_context(), configured, "x", "logs"), expected)

    def test_an_operators_own_folder_is_kept(self) -> None:
        chosen = str(Path("/operator/logs").resolve())
        self.assertEqual(runtime_path_setting(_context(), chosen, "x", "logs"), Path(chosen))

    def test_shipped_settings_pin_no_log_folder_in_the_program_files(self) -> None:
        settings = json.loads(
            (PROJECT_ROOT / "study_content" / "settings" / "hardware_settings.json").read_text(encoding="utf-8")
        )

        def log_dirs(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "log_dir" and isinstance(value, str):
                        yield value
                    yield from log_dirs(value)

        for value in log_dirs(settings):
            with self.subTest(log_dir=value):
                self.assertFalse(value.replace("\\", "/").startswith("study_runner/"), value)


if __name__ == "__main__":
    unittest.main()
