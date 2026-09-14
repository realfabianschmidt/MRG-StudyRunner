"""Is this a packaged build, and where does it live?

Moved out of the former backend settings package during the 1.0 rebuild
(docs/archive/architecture-1.0-umbau.md, Phase 2.1). Extensions and
`plugin_framework` need to know whether they are running frozen, and this
dependency-light module keeps that question outside the HTTP server.

`runtime_core.settings.runtime_config` re-exports all three functions for
runtime callers.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from study_runner.shared.software_root import find_software_root


def is_frozen() -> bool:
    """Return True when running from a packaged (PyInstaller) build.

    Single source of truth: helpers that need to behave differently in packaged
    builds must call this instead of checking sys.frozen themselves.
    """
    return bool(getattr(sys, "frozen", False))


def get_project_base_dir() -> Path:
    """Return the folder that contains bundled project resources."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    return find_software_root(Path(__file__))


def get_app_mode() -> str:
    configured_mode = os.getenv("STUDY_RUNNER_APP_MODE", "").strip().lower()
    if configured_mode:
        return configured_mode
    return "packaged" if is_frozen() else "python"
