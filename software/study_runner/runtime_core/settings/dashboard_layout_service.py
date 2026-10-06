"""The operator's arrangement of dashboard tiles, kept per computer.

The dashboard shows its plugin tiles in two columns. An operator can drag
them into the order that suits their work; this file remembers that order
for this computer (``settings/dashboard_layout.local.json``), so every browser
and every restart shows the same arrangement. It holds plugin keys and reserved
panel keys, never participant data.

A key that no longer belongs to an installed plugin is simply ignored by the
dashboard, and a new plugin is placed by the dashboard's default rule, so
the file never needs migrating.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from study_runner.shared.atomic_io import atomic_write_json

LAYOUT_FILE = "dashboard_layout.local.json"
COLUMNS = 2
MAX_KEYS = 64
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class DashboardLayoutError(ValueError):
    """A layout the dashboard sent that cannot be stored."""


def layout_path(settings_dir: Path) -> Path:
    return Path(settings_dir) / LAYOUT_FILE


def load_layout(settings_dir: Path) -> dict[str, Any]:
    """The stored layout, or ``{"columns": None}`` when none (or a broken one) is stored."""
    try:
        raw = json.loads(layout_path(settings_dir).read_text(encoding="utf-8"))
        return {"columns": _normalize(raw)["columns"]}
    except (OSError, ValueError):
        return {"columns": None}


def save_layout(settings_dir: Path, payload: Any) -> dict[str, Any]:
    """Validate and store a layout; returns what was stored."""
    layout = _normalize(payload)
    atomic_write_json(layout_path(settings_dir), layout)
    return layout


def reset_layout(settings_dir: Path) -> dict[str, Any]:
    """Forget the arrangement: the dashboard goes back to its default order."""
    try:
        layout_path(settings_dir).unlink()
    except FileNotFoundError:
        pass
    return {"columns": None}


def _normalize(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("columns"), list):
        raise DashboardLayoutError("The layout needs a list of columns.")
    columns = payload["columns"]
    if len(columns) != COLUMNS:
        raise DashboardLayoutError(f"The layout needs exactly {COLUMNS} columns.")
    seen: set[str] = set()
    normalized: list[list[str]] = []
    for column in columns:
        if not isinstance(column, list):
            raise DashboardLayoutError("Each column must be a list of tile keys.")
        keys: list[str] = []
        for key in column:
            if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
                raise DashboardLayoutError("A plugin key must be lowercase snake_case.")
            if key in seen:
                raise DashboardLayoutError(f"The plugin {key!r} appears twice.")
            seen.add(key)
            keys.append(key)
        normalized.append(keys)
    if len(seen) > MAX_KEYS:
        raise DashboardLayoutError(f"A layout holds at most {MAX_KEYS} plugins.")
    return {"columns": normalized}
