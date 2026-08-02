from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is only required for the reliable event queue unit test")
class ReliableEventQueueJavaScriptTests(unittest.TestCase):
    def test_start_stop_order_and_durable_retry(self) -> None:
        subprocess.run(
            [shutil.which("node"), str(PROJECT_ROOT / "tests" / "js" / "reliable-event-queue.test.mjs")],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
