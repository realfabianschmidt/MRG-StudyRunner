from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is only required for the finalization monitor unit test")
class FinalizationViewModelJavaScriptTests(unittest.TestCase):
    def test_progress_priority_and_retryable_steps(self) -> None:
        subprocess.run(
            [shutil.which("node"), str(PROJECT_ROOT / "tests" / "js" / "finalization-view-model.test.mjs")],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
