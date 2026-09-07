"""Locate the `software/` folder without counting directory levels.

Several modules need the software root: to read `constraints/`, to build a
subprocess `PYTHONPATH`, or to resolve `study_content/` and `saved_results/`.
Each used to spell that as `Path(__file__).resolve().parents[N]`.

That arithmetic is silent when it is wrong. Move a module one level deeper and
`parents[4]` keeps returning *a* directory -- just the wrong one. Nothing
raises, no test fails, and `get_project_base_dir()` starts resolving study
content and results one folder too high. It surfaces much later as "where did
all my studies go", typically on an operator's machine after an update.

Searching upward for a marker cannot be wrong by one. Either the folder holding
`server.py` and `study_runner/` is found, or the caller gets an exception that
names what it looked for. `release_tools/pyinstaller/study_runner_server_common.py`
already locates the root this way for the packaged build; this is the same rule
for the application itself.

Packaged builds do not use this: `is_frozen()` callers resolve `sys._MEIPASS`
before they get here.
"""
from __future__ import annotations

from pathlib import Path


def find_software_root(start: Path | None = None) -> Path:
    """Return the `software/` folder that holds `server.py` and `study_runner/`.

    Walks upward from `start` (this file by default). Raises when no candidate
    matches, because a wrong root is worse than a missing one.
    """
    origin = Path(start) if start is not None else Path(__file__)
    origin = origin.resolve()
    candidates = (origin, *origin.parents) if origin.is_dir() else origin.parents
    for candidate in candidates:
        if is_software_root(candidate):
            return candidate
    raise RuntimeError(
        "Could not locate the software/ folder holding server.py and "
        f"study_runner/ above {origin}"
    )


def is_software_root(path: Path) -> bool:
    """Return True when `path` looks like the repository `software/` folder."""
    return (path / "server.py").exists() and (path / "study_runner").is_dir()
