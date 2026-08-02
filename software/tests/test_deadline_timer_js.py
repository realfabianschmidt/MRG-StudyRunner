from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is only required for the JS timer unit test")
class DeadlineTimerJavaScriptTests(unittest.TestCase):
    def test_monotonic_deadline_survives_throttled_callback(self) -> None:
        subprocess.run(
            [shutil.which("node"), str(PROJECT_ROOT / "tests" / "js" / "deadline-timer.test.mjs")],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
