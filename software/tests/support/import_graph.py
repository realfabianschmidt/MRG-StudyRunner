"""AST-based import graph for enforcing package boundaries.

String-matching (`"study_runner.apps.server" in text`) misses the shape most
real violations take: 8 of the 11 import-boundary violations known at the
start of the 1.0 rebuild (docs/architecture-1.0-umbau.md) are function-local
imports, deliberately placed inside a function body to dodge a cycle at
module-load time rather than at call time. A textual scan finds those only
by accident; walking the AST finds them by construction, at any nesting
depth, regardless of whether the import sits behind a branch, a
try/except, or a function body.

This does not replace `test_area_boundaries.py`'s subprocess-blocker
technique -- that one catches *transitive* edges (module A calls a function
in module B, which itself imports the forbidden module three frames down)
that no static AST walk can see, because the forbidden name never appears
as a literal import in A's own source. Use both: this module for "does this
file's own source import something it shouldn't", the subprocess blocker for
"can this file be loaded at all without the forbidden package becoming
importable transitively".
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


PACKAGE_ROOT_NAME = "study_runner"


@dataclass(frozen=True)
class ImportEdge:
    """A dependency of an import statement, including relative imports.

    A from-import depends on its base package and may load a named submodule.
    Both edges are reported when that submodule exists in the source tree.

    `names` holds the specific names pulled in by a `from X import a, b`
    statement (empty for a plain `import X`). Needed to tell "this file
    probes the native core" from "this file writes through it" when both
    live in the same module -- the module-level edge alone can't distinguish
    `from .core import probe_core_library` from `from .core import
    NativeXdfWriter`.
    """

    file: Path
    lineno: int
    imported_module: str
    names: tuple[str, ...] = ()

    @property
    def area(self) -> str | None:
        return module_area(self.imported_module)


def module_area(dotted_module: str) -> str | None:
    """First-level `study_runner` subpackage a dotted module belongs to.

    `study_runner.apps.server.services.x` -> `"backend"`. Anything outside
    `study_runner` -> `None`.

    A two-segment name like `study_runner.version` returns `"version"` here
    even though `version.py` is a bare top-level module, not a subpackage --
    this function has no filesystem access, so it cannot tell "the second
    segment names a real subpackage" from "the second segment names a
    sibling top-level module" purely from the dotted string; both look
    identical (exactly two segments). Callers that need that distinction
    (e.g. deciding whether an imported name is a real *area*) must intersect
    the result against their own known-directory set, the way
    `file_area` below does with real filesystem information.
    """
    parts = dotted_module.split(".")
    if len(parts) < 2 or parts[0] != PACKAGE_ROOT_NAME:
        return None
    return parts[1]


def file_area(path: Path, *, package_root: Path) -> str | None:
    """First-level area the source file itself belongs to.

    Unlike `module_area`, this has real filesystem information and uses it:
    a file needs at least three path segments under `package_root`
    (`study_runner/<area>/<file>.py`) to belong to an area. A file sitting
    directly in `study_runner/` (`version.py`, `app_server.py`,
    `self_check.py`, `__init__.py`) has only two segments and is correctly
    `None` -- it is not part of any area's line count or import-boundary
    rules. Getting this off by one silently pulled `study_runner/__init__.py`
    into a fictitious `"__init__.py"` area the first time this function was
    used for something that iterates *files* rather than checking one
    already-known path (`tools/measure_structure.py`); this docstring and the
    `len(parts) < 3` check are what's left of that bug.
    """
    try:
        relative = path.resolve().relative_to(package_root.resolve())
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 3 or parts[0] != PACKAGE_ROOT_NAME:
        return None
    return parts[1]


def module_path_of(path: Path, *, package_root: Path) -> str:
    """Absolute dotted module path a source file corresponds to.

    `package_root` is the directory that directly contains `study_runner/`
    (i.e. `software/`). Needed to resolve relative imports against the
    importing file's own location, the same way Python's import system does.
    """
    relative = path.resolve().relative_to(package_root.resolve())
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _own_package(path: Path, module: str) -> str:
    """The dotted package a relative import inside `path` resolves against.

    An `__init__.py` file *is* its package for import-resolution purposes;
    every other module resolves relative imports against its parent package.
    Getting this wrong silently shifts every relative-import resolution by
    one level for every package `__init__.py` -- exactly the class of bug
    this module exists to avoid introducing elsewhere.
    """
    if path.name == "__init__.py":
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""


def _resolve_relative(*, own_package: str, level: int, module: str | None) -> str:
    base_parts = own_package.split(".") if own_package else []
    up = level - 1  # `from . import x` (level=1) resolves against own_package directly.
    if up:
        base_parts = base_parts[:-up] if up <= len(base_parts) else []
    base = ".".join(base_parts)
    if module:
        return f"{base}.{module}" if base else module
    return base


def iter_imports(path: Path, *, package_root: Path) -> Iterator[ImportEdge]:
    """Yield dependencies at every nesting depth without importing any code.

    For ``from study_runner import backend``, checking only the base package
    misses the backend dependency. Resolve named modules against the source
    tree, while retaining ordinary imported attributes in the base edge's
    ``names``. A package can shadow a submodule with an attribute at runtime;
    conservatively include the existing submodule in that ambiguous case.
    """
    # utf-8-sig tolerates a leading BOM (at least one file in this tree has
    # one); plain utf-8 makes ast.parse raise on it instead of on anything
    # meaningful.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    own_module = module_path_of(path, package_root=package_root)
    own_package = _own_package(path, own_module)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield ImportEdge(file=path, lineno=node.lineno, imported_module=alias.name)
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            resolved = (
                _resolve_relative(own_package=own_package, level=node.level, module=node.module)
                if node.level
                else node.module
            )
            if not resolved:
                continue
            yield ImportEdge(file=path, lineno=node.lineno, imported_module=resolved, names=names)
            if resolved != PACKAGE_ROOT_NAME and not resolved.startswith(PACKAGE_ROOT_NAME + "."):
                continue
            for name in names:
                if name == "*":
                    continue
                child = f"{resolved}.{name}"
                child_path = package_root.joinpath(*child.split("."))
                if child_path.with_suffix(".py").is_file() or child_path.is_dir():
                    yield ImportEdge(file=path, lineno=node.lineno, imported_module=child)


def iter_python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path
