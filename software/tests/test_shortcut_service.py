from __future__ import annotations

from pathlib import Path
import os
import shlex
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.settings import shortcut_service


class ShortcutServiceTests(unittest.TestCase):
    def test_source_launch_uses_python_and_server_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir).resolve()
            server_file = base_dir / "server.py"
            server_file.write_text("print('study runner')\n", encoding="utf-8")

            target, arguments, working_dir = shortcut_service._server_launch({"BASE_DIR": str(base_dir)})

        self.assertEqual(target, Path(sys.executable).resolve())
        self.assertEqual(arguments, str(server_file))
        self.assertEqual(working_dir, base_dir)

    def test_unsupported_platform_returns_clear_error(self) -> None:
        with patch("study_runner.runtime_core.settings.shortcut_service.platform.system", return_value="Linux"):
            with self.assertRaises(shortcut_service.ShortcutError) as raised:
                shortcut_service.create_desktop_shortcut({"BASE_DIR": "."})

        self.assertIn("Windows and macOS", str(raised.exception))

    def test_windows_arguments_are_quoted_for_paths_with_spaces(self) -> None:
        self.assertEqual(
            shortcut_service._windows_arguments(r"C:\Study Runner\software\server.py"),
            r'"C:\Study Runner\software\server.py"',
        )

    def test_macos_source_shortcut_uses_official_start_script_and_is_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            # resolve(): macOS temp dirs live behind the /var -> /private/var symlink.
            root = Path(temp_dir).resolve() / "Study Runner's (3)"
            base_dir = root / "software"
            start_script = root / "tools" / "start-macos.sh"
            desktop = Path(temp_dir) / "Desktop"
            result_file = base_dir / "saved_results" / "keep.txt"
            base_dir.mkdir(parents=True)
            start_script.parent.mkdir(parents=True)
            start_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            result_file.parent.mkdir()
            result_file.write_text("keep this result\n", encoding="utf-8")
            desktop.mkdir()

            with patch.object(shortcut_service, "_desktop_dir", return_value=desktop):
                with patch.object(shortcut_service.platform, "system", return_value="Darwin"):
                    first = shortcut_service.create_desktop_shortcut({"BASE_DIR": str(base_dir)})
                    shortcut_path = Path(first["path"])
                    shortcut_path.write_text("stale launcher\n", encoding="utf-8")
                    second = shortcut_service.create_desktop_shortcut({"BASE_DIR": str(base_dir)})

            content = shortcut_path.read_text(encoding="utf-8")
            self.assertEqual(first, second)
            self.assertEqual(shortcut_path.name, "Study Runner.command")
            self.assertEqual(
                content,
                f"#!/bin/zsh\nexec /bin/bash {shlex.quote(str(start_script))}\n",
            )
            self.assertNotIn("stale launcher", content)
            if os.name != "nt":
                self.assertTrue(shortcut_path.stat().st_mode & stat.S_IXUSR)
            self.assertEqual(result_file.read_text(encoding="utf-8"), "keep this result\n")


if __name__ == "__main__":
    unittest.main()
