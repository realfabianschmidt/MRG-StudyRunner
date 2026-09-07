"""Pin the 1.0 rebuild's package-boundary invariants as a shrinking allowlist.

Target doc `MRG_Recorder_Core_Architektur_1.0.md` section 14 lists import
invariants the eventual `apps/packages/extensions` split must hold. Writing
them as tests that are simply red until Phase 4 moves files would either sit
permanently red in CI (training everyone to ignore red, and blocking a merge
to `main`) or get skipped (which is the same as not having them). Neither
gives Codex or a second agent working in parallel a way to see, commit by
commit, whether a change made things better or worse.

Instead: `KNOWN_VIOLATIONS` lists every current violation, found mechanically
(see the discovery script in docs/architecture-1.0-umbau.md Phase 1.2 -- not
hand-transcribed, which already missed two real edges once: this file's
predecessor list had 17 pairs; the actual tree has 18, including one the
architecture doc's own invariant list doesn't mention --
`backend/services/recording` importing `recording_worker` directly, the
future data_core host importing the future data_core worker).

This test fails two ways, deliberately:
  - a violation exists that is NOT in KNOWN_VIOLATIONS -- something got
    worse, possibly a new plugin or a careless refactor.
  - a KNOWN_VIOLATIONS entry does NOT correspond to an actual violation
    anymore -- something got fixed. Good, but the list lies until the entry
    is deleted, and a stale entry is exactly how a boundary quietly stops
    being enforced. Delete the entry in the same commit that fixes the code.

`RULES` encodes path-prefix -> forbidden-module-prefix, not just top-level
area -> area, because the sharpest edges do not align with today's directory
boundaries: `backend/services/recording/` is the future data_core *host* and
must not import `recording_worker` (the future data_core *worker*), but nothing
else under `backend` is restricted that way -- it legitimately talks to most
of the application. Once Phase 4 physically separates these, the prefixes
below become the new top-level package names and this file's RULES collapse
to plain area checks; nothing about the enforcement changes.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from support.import_graph import iter_imports, iter_python_files  # noqa: E402


STUDY_RUNNER_ROOT = PROJECT_ROOT / "study_runner"

# (source path prefix under study_runner/, forbidden imported-module prefix)
RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("plugin_framework",), "study_runner.backend"),
    (("plugin_framework",), "study_runner.recording_worker"),
    (("plugin_framework",), "study_runner.recording"),
    (("plugins",), "study_runner.backend"),
    (("plugins",), "study_runner.recording"),
    (("plugins",), "study_runner.recording_worker"),
    (("recording",), "study_runner.recording_worker"),
    (("recording",), "study_runner.plugin_framework"),
    (("recording_worker",), "study_runner.recording"),
    (("recording_worker",), "study_runner.plugin_framework"),
    (("recording_worker",), "study_runner.backend"),
    (("backend", "services", "recording"), "study_runner.recording_worker"),
)

# (file relative to study_runner/, imported module). One entry per distinct
# (file, module) pair, regardless of how many lines in that file import it --
# the invariant is "this file must not couple to that module", not "this file
# must not couple to that module more than once".
KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "backend/services/recording/recording_runtime.py",
            "study_runner.recording_worker.lsl_recording",
        ),
        ("plugin_framework/dependency_utils.py", "study_runner.backend.services.settings.runtime_config"),
        ("plugin_framework/plugin_api.py", "study_runner.backend.services.studies.study_secrets_service"),
        ("plugins/brainbit/adapter.py", "study_runner.backend.services.settings.runtime_config"),
        ("plugins/brainbit/plugin.py", "study_runner.backend.services.settings.runtime_config"),
        ("plugins/camera_emotion/worker/plugin.py", "study_runner.backend.services.settings.runtime_config"),
        ("plugins/notion_upload/adapter.py", "study_runner.backend.services.studies.validation"),
        ("plugins/notion_upload/adapter.py", "study_runner.backend.services.studies.study_config_service"),
        ("plugins/notion_upload/adapter.py", "study_runner.backend.services.studies.study_plugin_config"),
        ("recording/clock_diagnostics.py", "study_runner.plugin_framework.dependency_utils"),
        ("recording/clock_diagnostics.py", "study_runner.plugin_framework.plugin_catalog"),
        ("recording/markers.py", "study_runner.plugin_framework.dependency_utils"),
        ("recording/markers.py", "study_runner.plugin_framework.plugin_catalog"),
        ("recording/worker_binary.py", "study_runner.recording_worker.core"),
        ("recording_worker/application.py", "study_runner.recording.worker_protocol"),
        ("recording_worker/lsl_recording.py", "study_runner.recording.backup"),
        ("recording_worker/runtime.py", "study_runner.recording.worker_protocol"),
        ("recording_worker/runtime.py", "study_runner.recording.recovery"),
    }
)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _find_violations() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in iter_python_files(STUDY_RUNNER_ROOT):
        relative_parts = path.relative_to(STUDY_RUNNER_ROOT).parts
        for source_prefix, forbidden in RULES:
            if relative_parts[: len(source_prefix)] != source_prefix:
                continue
            for edge in iter_imports(path, package_root=PROJECT_ROOT):
                if _matches_prefix(edge.imported_module, forbidden):
                    rel_file = path.relative_to(STUDY_RUNNER_ROOT).as_posix()
                    found.add((rel_file, edge.imported_module))
    return found


class ImportBoundaryTests(unittest.TestCase):
    def test_no_new_boundary_violations(self) -> None:
        actual = _find_violations()
        new_violations = actual - KNOWN_VIOLATIONS
        self.assertEqual(
            new_violations,
            set(),
            "new import-boundary violation(s), not in KNOWN_VIOLATIONS: "
            + ", ".join(f"{file} -> {module}" for file, module in sorted(new_violations)),
        )

    def test_known_violations_list_has_no_stale_entries(self) -> None:
        actual = _find_violations()
        fixed = KNOWN_VIOLATIONS - actual
        self.assertEqual(
            fixed,
            set(),
            "these KNOWN_VIOLATIONS entries no longer match a real import -- "
            "delete them from the list in the same commit that fixed the code: "
            + ", ".join(f"{file} -> {module}" for file, module in sorted(fixed)),
        )


if __name__ == "__main__":
    unittest.main()
