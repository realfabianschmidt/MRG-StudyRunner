"""The areas may not import each other into a knot.

`recording/` once reached into `backend.services` for crash-safe writes. Because
`backend/__init__.py` is the Flask app factory and eagerly imports every route,
that single helper import pulled the whole web application in behind it -- and
one of those routes imported back into `recording`, so it was a genuine cycle
too. It stayed invisible while something always imported the backend first, and
broke the moment a tool wanted only to read a session: the native-core CI job
could not even load its own test module, failing on `No module named 'flask'`.

These tests pin the boundary rather than the symptom.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _imports_cleanly_without(modules: tuple[str, ...], blocked: str) -> subprocess.CompletedProcess:
    """Import modules in a fresh interpreter where `blocked` cannot be imported."""
    script = f"""
import sys

class _Blocker:
    def find_module(self, name, path=None):
        if name == {blocked!r} or name.startswith({blocked!r} + "."):
            return self
        return None

    def load_module(self, name):
        raise ImportError("No module named " + repr(name))

sys.meta_path.insert(0, _Blocker())
for name in {modules!r}:
    __import__(name)
print("ok")
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


class AreaBoundaryTests(unittest.TestCase):
    def test_recording_does_not_need_the_web_application(self) -> None:
        """Reading or writing a session must not construct the Flask app."""
        result = _imports_cleanly_without(
            (
                "study_runner.recording.artifacts",
                "study_runner.recording.coordinator",
                "study_runner.recording.recovery",
                "study_runner.recording.worker_protocol",
                "study_runner.recording.xdf",
            ),
            "flask",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_detached_worker_does_not_need_the_web_application(self) -> None:
        """It runs as its own process; Flask is not installed in its CI job."""
        result = _imports_cleanly_without(
            ("study_runner.recording_worker.runtime", "study_runner.recording_worker.core"),
            "flask",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_depends_on_no_area(self) -> None:
        """That is the only thing that makes it safe for every area to use."""
        shared = PROJECT_ROOT / "study_runner" / "shared"
        offenders = []
        for path in shared.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for area in ("backend", "frontend", "recording", "plugins", "plugin_framework", "updates"):
                if f"study_runner.{area}" in text:
                    offenders.append(f"{path.name} imports study_runner.{area}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
