"""Structure ratchet for the 1.0 rebuild.

Measures four numbers named in `MRG_Recorder_Core_Architektur_1.0.md`
section 14: cross-package import edges, import cycles, lines per package,
and the largest file. `--check` fails only when a number gets WORSE than a
committed baseline (tools/structure_baseline.json) -- not against an
absolute threshold. An absolute threshold set today would either do nothing
(several of today's numbers already reflect real, known, and still-being-fixed
violations -- see docs/architecture-1.0-umbau.md's KNOWN_VIOLATIONS) or block
every commit before Phase 2 even starts.

"Package" means today's `study_runner/<area>/` directories, plus the
separate `data_core/{host,worker,contract}` packages once they exist. Their
boundaries remain visible after the directory move. Before updating a
baseline for intentional feature growth or a package move, review the full
metric delta alongside the change; a passing check is never a reason to
automatically overwrite the baseline. An area that appears in the current
measurement but not in the baseline (a genuinely new package from an
intentional restructuring) is reported but does not fail the check; an area
whose line count grows past its own recorded baseline does.

Usage:
    python tools/measure_structure.py                  # print current metrics as JSON
    python tools/measure_structure.py --check           # fail (exit 1) on regression vs. the baseline
    python tools/measure_structure.py --write-baseline   # record current metrics as the new baseline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPO_ROOT / "software"
STUDY_RUNNER_ROOT = SOFTWARE_ROOT / "study_runner"
BASELINE_PATH = Path(__file__).resolve().parent / "structure_baseline.json"

sys.path.insert(0, str(SOFTWARE_ROOT))
sys.path.insert(0, str(SOFTWARE_ROOT / "tests"))

from support.import_graph import iter_imports, iter_python_files, module_path_of  # noqa: E402


def _areas() -> list[str]:
    areas = {
        path.name
        for path in STUDY_RUNNER_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    for name in ("host", "worker", "contract"):
        if (STUDY_RUNNER_ROOT / "data_core" / name).is_dir():
            areas.add(f"data_core.{name}")
    return sorted(areas)


def _area_for_module(module: str, areas: list[str]) -> str | None:
    """Choose the most specific real area; top-level .py files are not areas."""
    for area in sorted(areas, key=len, reverse=True):
        prefix = "study_runner." + area
        if module == prefix or module.startswith(prefix + "."):
            return area
    return None


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        return sum(1 for _ in handle)


def measure() -> dict:
    areas = _areas()
    lines_per_area: dict[str, int] = {area: 0 for area in areas}
    largest_file = {"path": "", "lines": 0}
    cross_package_import_edges: set[tuple[Path, int, str]] = set()
    direct_reach: dict[str, set[str]] = {area: set() for area in areas}

    for path in iter_python_files(STUDY_RUNNER_ROOT):
        area = _area_for_module(module_path_of(path, package_root=SOFTWARE_ROOT), areas)
        if area is None:
            continue
        lines = _line_count(path)
        lines_per_area[area] = lines_per_area.get(area, 0) + lines
        if lines > largest_file["lines"]:
            largest_file = {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "lines": lines,
            }

        for edge in iter_imports(path, package_root=SOFTWARE_ROOT):
            target_area = _area_for_module(edge.imported_module, areas)
            if target_area is None or target_area == area:
                continue
            # A from-import's base and child modules can belong to the same
            # area. Count that statement-to-area dependency once.
            cross_package_import_edges.add((path, edge.lineno, target_area))
            direct_reach[area].add(target_area)

    # Transitive closure over the (small) area graph: a cycle can route
    # through a third area, so direct mutual edges alone would undercount it.
    reach = {area: set(targets) for area, targets in direct_reach.items()}
    changed = True
    while changed:
        changed = False
        for area in areas:
            for mid in list(reach[area]):
                for target in reach.get(mid, ()):
                    if target != area and target not in reach[area]:
                        reach[area].add(target)
                        changed = True

    cycle_pairs = sorted(
        {
            tuple(sorted((a, b)))
            for a in areas
            for b in reach[a]
            if a != b and a in reach.get(b, ())
        }
    )

    return {
        "cross_package_import_edges": len(cross_package_import_edges),
        "cycle_count": len(cycle_pairs),
        "cycle_pairs": [list(pair) for pair in cycle_pairs],
        "lines_per_package": lines_per_area,
        "largest_file": largest_file,
    }


def check(current: dict, baseline: dict) -> list[str]:
    problems = []

    if current["cross_package_import_edges"] > baseline["cross_package_import_edges"]:
        problems.append(
            "cross-package import edges grew: "
            f"{baseline['cross_package_import_edges']} -> {current['cross_package_import_edges']}"
        )

    new_cycles = {tuple(pair) for pair in current["cycle_pairs"]} - {
        tuple(pair) for pair in baseline["cycle_pairs"]
    }
    if new_cycles:
        problems.append(
            f"import cycles changed: {baseline['cycle_count']} -> {current['cycle_count']} "
            f"(new: {sorted(new_cycles)})"
        )

    for area, lines in sorted(current["lines_per_package"].items()):
        baseline_lines = baseline["lines_per_package"].get(area)
        if baseline_lines is not None and lines > baseline_lines:
            problems.append(f"{area}/ grew: {baseline_lines} -> {lines} lines")

    if current["largest_file"]["lines"] > baseline["largest_file"]["lines"]:
        problems.append(
            "largest file grew: "
            f"{baseline['largest_file']['path']} ({baseline['largest_file']['lines']} lines) -> "
            f"{current['largest_file']['path']} ({current['largest_file']['lines']} lines)"
        )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Fail on regression vs. the committed baseline.")
    parser.add_argument("--write-baseline", action="store_true", help="Record current metrics as the new baseline.")
    args = parser.parse_args()

    current = measure()

    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote baseline to {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return 0

    if args.check:
        if not BASELINE_PATH.is_file():
            print(f"No baseline at {BASELINE_PATH.relative_to(REPO_ROOT)}; run --write-baseline first.")
            return 1
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        problems = check(current, baseline)
        if problems:
            print("Structure regression(s):")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("Structure metrics are at or better than the baseline.")
        return 0

    print(json.dumps(current, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
