"""study_runner/updates/installer.py: the detached restart helper.

Only the source-mode branch is new here; the packaged branch's own
behaviour is unchanged and stays covered by test_update_service.py's
end-to-end zip-staging test, which exercises request_update_install's
packaged path through to a real staged executable.
"""
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

from study_runner.updates import installer


def _write_state(state_file: Path, base_dir: Path) -> None:
    state_file.write_text(
        json.dumps(
            {
                "staged": {"mode": "source", "version": "1.1.0"},
                "source_restart": {"base_dir": str(base_dir)},
            }
        ),
        encoding="utf-8",
    )


class InstallerSourceRestartTests(unittest.TestCase):
    def test_restarts_a_source_checkout_by_relaunching_server_py(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base_dir = Path(temp) / "software"
            base_dir.mkdir()
            (base_dir / "server.py").write_text("# stub\n", encoding="utf-8")
            state_file = Path(temp) / "update-state.json"
            _write_state(state_file, base_dir)

            with patch.object(installer, "_spawn_detached") as spawn, patch.object(installer.time, "sleep"):
                exit_code = installer.main([str(state_file)])

            self.assertEqual(exit_code, 0)
            spawn.assert_called_once()
            cmd, cwd, _env = spawn.call_args[0]
            self.assertEqual(cmd, [sys.executable, str(base_dir / "server.py")])
            self.assertEqual(Path(cwd), base_dir)

            saved_state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_state["state"], "applied")

    def test_fails_closed_when_server_py_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base_dir = Path(temp) / "software"
            base_dir.mkdir()  # no server.py written
            state_file = Path(temp) / "update-state.json"
            _write_state(state_file, base_dir)

            with patch.object(installer, "_spawn_detached") as spawn, patch.object(installer.time, "sleep"):
                exit_code = installer.main([str(state_file)])

            self.assertEqual(exit_code, 1)
            spawn.assert_not_called()
            saved_state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_state["state"], "install_failed")
            self.assertIn("server.py", saved_state["error"])


if __name__ == "__main__":
    unittest.main()
