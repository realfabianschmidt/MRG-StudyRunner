"""Install an optional runtime dependency on demand, or fail with a plain message.

Moved out of `plugin_framework.dependency_utils` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.4): `recording/markers.py` and
`recording/clock_diagnostics.py` need this to lazily require `pylsl`, and
`recording` (data_core) may not depend on `plugin_framework` (invariant #2,
extended in this rebuild to cover this direction too -- see the doc's
Ist/Soll table). This module was already fully self-contained (stdlib plus
`shared.runtime_mode`), so the whole thing moved rather than splitting it.

`plugin_framework.dependency_utils` re-exports this so its six existing
plugin callers keep working unchanged.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from typing import Iterable

from study_runner.shared.runtime_mode import get_app_mode


def ensure_requirements(
    requirements: Iterable[tuple[str, str]],
    *,
    auto_install: bool,
    label: str,
) -> bool:
    """Ensure a plugin's optional dependencies are available."""
    missing = [
        (module_name, package_name)
        for module_name, package_name in requirements
        if importlib.util.find_spec(module_name) is None
    ]

    if not missing:
        return True

    names = ", ".join(package_name for _, package_name in missing)
    print(f"[{label}] Missing dependency: {names}")

    if not auto_install or _runtime_pip_install_disabled():
        print(f"[{label}] Auto-install disabled. Install manually and restart the server.")
        return False

    for _, package_name in missing:
        try:
            print(f"[{label}] Installing {package_name} ...")
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    package_name,
                ]
            )
        except Exception as error:
            print(f"[{label}] Could not install {package_name}: {error}")
            return False

    still_missing = [
        package_name
        for module_name, package_name in missing
        if importlib.util.find_spec(module_name) is None
    ]
    if still_missing:
        print(f"[{label}] Dependency still missing after install: {', '.join(still_missing)}")
        return False

    print(f"[{label}] Optional dependencies are ready.")
    return True


def _runtime_pip_install_disabled() -> bool:
    disabled = os.getenv("STUDY_RUNNER_DISABLE_RUNTIME_PIP", "").strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return True

    return get_app_mode() in {"desktop", "packaged"}
