from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is only required for the JS settings unit test")
class StudySettingsJavaScriptTests(unittest.TestCase):
    def test_legacy_controls_and_v3_plugins_stay_in_sync(self) -> None:
        subprocess.run(
            [shutil.which("node"), str(PROJECT_ROOT / "tests" / "js" / "study-settings.test.mjs")],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
