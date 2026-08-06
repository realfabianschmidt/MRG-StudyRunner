from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.settings import shortcut_service


class ShortcutServiceTests(unittest.TestCase):
    def test_source_launch_uses_python_and_server_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            server_file = base_dir / "server.py"
            server_file.write_text("print('study runner')\n", encoding="utf-8")

            target, arguments, working_dir = shortcut_service._server_launch({"BASE_DIR": str(base_dir)})

        self.assertEqual(target, Path(sys.executable).resolve())
        self.assertEqual(arguments, str(server_file))
        self.assertEqual(working_dir, base_dir)

    def test_unsupported_platform_returns_clear_error(self) -> None:
        with patch("study_runner.backend.services.settings.shortcut_service.platform.system", return_value="Linux"):
            with self.assertRaises(shortcut_service.ShortcutError) as raised:
                shortcut_service.create_desktop_shortcut({"BASE_DIR": "."})

        self.assertIn("Windows and macOS", str(raised.exception))

    def test_windows_arguments_are_quoted_for_paths_with_spaces(self) -> None:
        self.assertEqual(
            shortcut_service._windows_arguments(r"C:\Study Runner\software\server.py"),
            r'"C:\Study Runner\software\server.py"',
        )


if __name__ == "__main__":
    unittest.main()
