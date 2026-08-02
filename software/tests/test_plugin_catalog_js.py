from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is only required for the plugin catalog unit test")
class PluginCatalogJavaScriptTests(unittest.TestCase):
    def test_capability_visibility_and_invalid_plugin_isolation(self) -> None:
        subprocess.run(
            [shutil.which("node"), str(PROJECT_ROOT / "tests" / "js" / "plugin-catalog.test.mjs")],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
