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
    def find_spec(self, name, path=None, target=None):
        if name == {blocked!r} or name.startswith({blocked!r} + "."):
            raise ImportError("Blocked architecture dependency: " + name)
        return None

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
    def test_import_blocker_rejects_the_forbidden_module_and_its_children(self) -> None:
        """A passing isolation test is meaningful only if its blocker works."""
        for module, blocked in (("fractions", "fractions"), ("xml.etree.ElementTree", "xml.etree")):
            with self.subTest(module=module, blocked=blocked):
                result = _imports_cleanly_without((module,), blocked)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Blocked architecture dependency: " + blocked, result.stderr)

    def test_recording_does_not_need_the_web_application(self) -> None:
        """Reading or writing a session must not construct the Flask app."""
        result = _imports_cleanly_without(
            (
                "study_runner.data_core.host.artifacts",
                "study_runner.data_core.host.coordinator",
                "study_runner.data_core.contract.recording_lease",
                "study_runner.data_core.contract.worker_protocol",
                "study_runner.data_core.host.xdf",
            ),
            "flask",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_detached_worker_does_not_need_the_web_application(self) -> None:
        """It runs as its own process; Flask is not installed in its CI job."""
        result = _imports_cleanly_without(
            ("study_runner.data_core.worker.runtime", "study_runner.data_core.worker.core"),
            "flask",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_detached_worker_does_not_import_the_host_side_recording_package(self) -> None:
        """host and worker may not import each other (invariant #1).

        Stronger than blocking `flask`: this blocks `study_runner.data_core.host`
        itself. Before Phase 2.5 (docs/architecture-1.0-umbau.md), the worker
        genuinely could not have loaded here -- `worker_protocol`, `backup`
        and `recovery` lived under `recording/` and the worker imported them
        directly. They moved to `shared/`; this is the test that would have
        caught it if they hadn't.
        """
        result = _imports_cleanly_without(
            (
                "study_runner.data_core.worker.application",
                "study_runner.data_core.worker.runtime",
                "study_runner.data_core.worker.core",
                "study_runner.data_core.worker.lsl_recording",
            ),
            "study_runner.data_core.host",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_host_side_recording_package_does_not_import_the_worker(self) -> None:
        """The other half of invariant #1: `recording/` may not import
        `recording_worker` either -- it only probes a built binary
        (`worker_binary.py`) and talks to a running one over loopback HTTP
        (`worker_protocol.py`), never imports the worker's own process code.
        """
        result = _imports_cleanly_without(
            (
                "study_runner.data_core.host.artifacts",
                "study_runner.data_core.host.coordinator",
                "study_runner.data_core.contract.recording_lease",
                "study_runner.data_core.host.worker_binary",
                "study_runner.data_core.contract.worker_protocol",
                "study_runner.data_core.host.xdf",
            ),
            "study_runner.data_core.worker",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_depends_on_no_area(self) -> None:
        """That is the only thing that makes it safe for every area to use."""
        shared = PROJECT_ROOT / "study_runner" / "shared"
        # rglob over a missing directory yields nothing, so a renamed `shared/`
        # would turn this into a test that passes by finding no files to check.
        # The 1.0 restructure moves this path; fail loudly when it does.
        self.assertTrue(
            shared.is_dir(),
            f"{shared} does not exist -- update this path in the same commit "
            "that moved it (see docs/architecture-1.0-umbau.md)",
        )
        offenders = []
        for path in shared.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for area in ("backend", "frontend", "recording", "plugins", "plugin_framework", "updates"):
                if f"study_runner.{area}" in text:
                    offenders.append(f"{path.name} imports study_runner.{area}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
