"""Structure ratchet for the current modular architecture.

Measures the Python and browser JavaScript structure: cross-package import
edges, import cycles, lines per package, and largest files. `--check` fails
when a number gets WORSE than a committed baseline
(`tools/structure_baseline.json`). Browser JavaScript also has a hard
1,000-line per-file ceiling so oversized controllers cannot be normalized into
a future baseline.

"Package" means the current `study_runner/<area>/` directories and the
separate `data_core/{host,worker,contract}` boundaries. Frontend code is split
into admin, participant, shared, settings, and card areas. Before updating a
baseline for intentional feature growth or a package move, review the full
metric delta alongside the change; a passing check is never a reason to
automatically overwrite the baseline. A genuinely new area is reported but
does not fail the check; an existing area's growth beyond its recorded
baseline does.

Usage:
    python tools/measure_structure.py                  # print current metrics as JSON
    python tools/measure_structure.py --check           # fail (exit 1) on regression vs. the baseline
    python tools/measure_structure.py --write-baseline   # record current metrics as the new baseline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPO_ROOT / "software"
STUDY_RUNNER_ROOT = SOFTWARE_ROOT / "study_runner"
BASELINE_PATH = Path(__file__).resolve().parent / "structure_baseline.json"
FRONTEND_AREAS = ("admin", "participant", "shared", "settings", "cards")
FRONTEND_FILE_LIMIT = 1_000
ES_MODULE_RE = re.compile(
    r"^\s*(?:import\s+(?:[\w*{},\s]+\s+from\s+)?|export\s+(?:[\w*{},\s]+\s+from\s+))"
    r"['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

sys.path.insert(0, str(SOFTWARE_ROOT))
sys.path.insert(0, str(SOFTWARE_ROOT / "tests"))

from support.import_graph import iter_imports, iter_python_files, module_path_of  # noqa: E402


def _areas() -> list[str]:
    areas = {
        path.name
        for path in STUDY_RUNNER_ROOT.iterdir()
        if path.is_dir()
        and not path.name.startswith("__")
        and any(
            source.suffix in {".py", ".js"} and "__pycache__" not in source.parts
            for source in path.rglob("*")
        )
    }
    for parent, children in {
        "apps": ("server", "ui", "cli"),
        "data_core": ("host", "worker", "contract"),
        "plugins": ("sensors", "cards", "destinations", "outputs"),
    }.items():
        for name in children:
            if (STUDY_RUNNER_ROOT / parent / name).is_dir():
                areas.add(f"{parent}.{name}")
    scripts_root = STUDY_RUNNER_ROOT / "apps" / "ui" / "scripts"
    for name in FRONTEND_AREAS:
        has_sources = (scripts_root / name).is_dir()
        if name == "cards":
            has_sources = has_sources or any(
                (STUDY_RUNNER_ROOT / "plugins" / "cards").rglob("*.js")
            )
        if has_sources:
            areas.add(f"apps.ui.{name}")
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


def _area_for_source(path: Path, areas: list[str]) -> str | None:
    if path.suffix == ".js":
        try:
            relative = path.relative_to(STUDY_RUNNER_ROOT / "apps" / "ui" / "scripts")
        except ValueError:
            relative = None
        if relative is not None:
            if relative.parts and f"apps.ui.{relative.parts[0]}" in areas:
                return f"apps.ui.{relative.parts[0]}"
            return "apps.ui" if "apps.ui" in areas else None
        try:
            plugin_relative = path.relative_to(STUDY_RUNNER_ROOT / "plugins")
        except ValueError:
            plugin_relative = None
        if plugin_relative is not None and plugin_relative.parts:
            # Card JavaScript is one frontend area even though each extension
            # owns its files beside the Python contract.
            if plugin_relative.parts[0] == "cards":
                return "apps.ui.cards"
            plugin_area = f"plugins.{plugin_relative.parts[0]}"
            return plugin_area if plugin_area in areas else None
        return None
    return _area_for_module(module_path_of(path, package_root=SOFTWARE_ROOT), areas)


def _iter_javascript_imports(path: Path):
    source = path.read_text(encoding="utf-8-sig", errors="replace")
    for match in ES_MODULE_RE.finditer(source):
        specifier = match.group(1)
        if specifier.startswith("/static/scripts/"):
            target = STUDY_RUNNER_ROOT / "apps" / "ui" / "scripts" / specifier.removeprefix("/static/scripts/")
        elif specifier.startswith("."):
            target = (path.parent / specifier).resolve()
        else:
            continue
        candidates = [target] if target.suffix else [target.with_suffix(".js"), target / "index.js"]
        resolved = next((candidate for candidate in candidates if candidate.is_file()), None)
        if resolved is not None:
            yield resolved, source.count("\n", 0, match.start()) + 1


def measure() -> dict:
    areas = _areas()
    lines_per_area: dict[str, int] = {area: 0 for area in areas}
    largest_file = {"path": "", "lines": 0}
    largest_file_per_area = {area: {"path": "", "lines": 0} for area in areas}
    cross_package_import_edges: set[tuple[Path, int, str]] = set()
    direct_reach: dict[str, set[str]] = {area: set() for area in areas}

    source_files = [*iter_python_files(STUDY_RUNNER_ROOT), *STUDY_RUNNER_ROOT.rglob("*.js")]
    for path in source_files:
        if "__pycache__" in path.parts:
            continue
        area = _area_for_source(path, areas)
        if area is None:
            continue
        lines = _line_count(path)
        lines_per_area[area] = lines_per_area.get(area, 0) + lines
        if lines > largest_file["lines"]:
            largest_file = {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "lines": lines,
            }

        if lines > largest_file_per_area[area]["lines"]:
            largest_file_per_area[area] = {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "lines": lines,
            }

        imports = (
            ((_area_for_module(edge.imported_module, areas), edge.lineno) for edge in iter_imports(path, package_root=SOFTWARE_ROOT))
            if path.suffix == ".py"
            else ((_area_for_source(target, areas), lineno) for target, lineno in _iter_javascript_imports(path))
        )
        for target_area, lineno in imports:
            if target_area is None or target_area == area:
                continue
            # A from-import's base and child modules can belong to the same
            # area. Count that statement-to-area dependency once.
            cross_package_import_edges.add((path, lineno, target_area))
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
        "largest_file_per_package": largest_file_per_area,
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

    baseline_largest_by_area = baseline.get("largest_file_per_package", {})
    for area, largest in sorted(current.get("largest_file_per_package", {}).items()):
        baseline_largest = baseline_largest_by_area.get(area)
        if baseline_largest and largest["lines"] > baseline_largest["lines"]:
            problems.append(
                f"largest file in {area}/ grew: {baseline_largest['path']} "
                f"({baseline_largest['lines']} lines) -> {largest['path']} ({largest['lines']} lines)"
            )
        if area.startswith("apps.ui.") and largest["lines"] > FRONTEND_FILE_LIMIT:
            problems.append(
                f"frontend file exceeds {FRONTEND_FILE_LIMIT} lines: "
                f"{largest['path']} ({largest['lines']} lines)"
            )

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
