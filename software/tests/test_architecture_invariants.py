"""Target-doc invariants (`MRG_Recorder_Core_Architektur_1.0.md` section 14)
that already hold today, pinned as green regression locks before the 1.0
package restructure moves anything.

Two invariants live here rather than in `test_import_boundaries.py`:

- #5, "only the DataCore writes XDF bytes" -- today's writer is
  `recording_worker.core.NativeXdfWriter`. This isn't an import-boundary
  question (several files legitimately import *something* from
  `recording_worker.core`, e.g. `recording/worker_binary.py` imports
  `probe_core_library` to validate the native library without writing to
  it); it's specifically about which files instantiate the writer class.
- #3, "`contracts/` imports nothing from the rest of the app" -- there is no
  `study_runner/contracts/` package yet (it is created during Phase 2/3).
  The test below activates automatically the moment the first file is placed
  there, rather than needing a follow-up reminder; until then it passes
  vacuously, which is correct and not a blind spot, since there is no code to
  hide a violation in.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from support.import_graph import iter_imports, iter_python_files  # noqa: E402


STUDY_RUNNER_ROOT = PROJECT_ROOT / "study_runner"

# Areas allowed to actually write XDF bytes through the native core today.
# `recording/worker_binary.py` is deliberately excluded: it imports
# `probe_core_library`/`CoreProbe`/`NativeXdfError` to validate the library,
# never `NativeXdfWriter` itself -- see the file-specific check below, which
# enforces that distinction precisely instead of exempting the whole file.
WRITER_ALLOWED_AREAS = {"recording_worker"}
WRITER_CLASS_NAME = "NativeXdfWriter"


class OnlyDataCoreWritesXdfBytesTests(unittest.TestCase):
    def test_native_xdf_writer_is_only_instantiated_in_the_worker_area(self) -> None:
        offenders: list[str] = []
        for path in iter_python_files(STUDY_RUNNER_ROOT):
            area = path.relative_to(STUDY_RUNNER_ROOT).parts[0]
            if area in WRITER_ALLOWED_AREAS:
                continue
            for edge in iter_imports(path, package_root=PROJECT_ROOT):
                if WRITER_CLASS_NAME in edge.names:
                    rel = path.relative_to(STUDY_RUNNER_ROOT).as_posix()
                    offenders.append(f"{rel}:{edge.lineno}")

        self.assertEqual(
            offenders,
            [],
            f"only {sorted(WRITER_ALLOWED_AREAS)} may import {WRITER_CLASS_NAME}: "
            + ", ".join(offenders),
        )


class ContractsDependsOnNothingTests(unittest.TestCase):
    def test_contracts_imports_nothing_from_the_rest_of_the_app(self) -> None:
        contracts_dir = STUDY_RUNNER_ROOT / "contracts"
        if not contracts_dir.is_dir():
            self.skipTest("study_runner/contracts/ does not exist yet (created in Phase 2/3)")

        other_areas = {
            path.relative_to(STUDY_RUNNER_ROOT).parts[0]
            for path in STUDY_RUNNER_ROOT.iterdir()
            if path.is_dir() and path.name != "contracts" and not path.name.startswith("__")
        }

        offenders: list[str] = []
        for path in iter_python_files(contracts_dir):
            for edge in iter_imports(path, package_root=PROJECT_ROOT):
                if edge.area in other_areas:
                    rel = path.relative_to(STUDY_RUNNER_ROOT).as_posix()
                    offenders.append(f"{rel}:{edge.lineno} -> {edge.imported_module}")

        self.assertEqual(
            offenders,
            [],
            "contracts/ must depend on no other area of the application: " + ", ".join(offenders),
        )


# Target doc section 6: "a global time is only ever computed at read/export
# time, never persisted." Not mechanically provable in the general case --
# nothing can prove a stored number was never derived from two clocks by
# inspecting a field name. What this catches instead: the moment a field is
# literally *named* like the thing the invariant forbids, which is exactly
# how the rule dies in practice -- one convenience field added under time
# pressure, and two years later nobody remembers it was an estimate. This
# denylist gives that field nowhere to hide by name.
#
# Deliberately does NOT flag legitimate persisted timestamps: a UTC anchor at
# session start/end, `client_clock_offset_ms` (an offset, not a merged time),
# `source_time`/`snapshot_at`/`timestamp_start` (each honestly one clock's
# reading). Those don't match any pattern below.
FORBIDDEN_TIME_FIELD_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"global_time",
        r"unified_time",
        r"unified_timestamp",
        r"synced_time",
        r"synchronized_time",
        r"absolute_time",
        r"canonical_time",
        r"merged_time",
    )
)


def _string_field_names(tree: ast.AST):
    """Yield (lineno, name) for every string literal used as a dict/JSON field
    name -- a dict key, a subscript index, or the first argument to
    `.get`/`.setdefault`/`.pop`. Deliberately excludes plain string literals,
    so prose in a docstring or comment ("global time" used as a worked
    example) cannot trigger this -- confirmed necessary in Phase 1.3, where
    the equivalent whole-file literal scan for plugin keys produced exactly
    that false-positive shape against real files in this codebase.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.lineno, key.value
        elif isinstance(node, ast.Subscript):
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                yield index.lineno, index.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"get", "setdefault", "pop"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    yield first.lineno, first.value


class NoPersistedGlobalTimeFieldTests(unittest.TestCase):
    def test_no_field_name_claims_a_computed_global_time(self) -> None:
        offenders: list[str] = []
        for path in iter_python_files(STUDY_RUNNER_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for lineno, name in _string_field_names(tree):
                if any(pattern.search(name) for pattern in FORBIDDEN_TIME_FIELD_PATTERNS):
                    rel = path.relative_to(STUDY_RUNNER_ROOT).as_posix()
                    offenders.append(f"{rel}:{lineno} field {name!r}")

        self.assertEqual(
            offenders,
            [],
            "a field name claims a computed global/unified/synced time -- "
            "persist the individual clock readings instead, and compute a "
            "global time only at read/export time: " + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
