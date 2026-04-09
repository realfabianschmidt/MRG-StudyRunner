from __future__ import annotations

import importlib.util
import subprocess
import sys
from typing import Iterable


def ensure_requirements(
    requirements: Iterable[tuple[str, str]],
    *,
    auto_install: bool,
    label: str,
) -> bool:
    """Ensure optional integration dependencies are available."""
    missing = [
        (module_name, package_name)
        for module_name, package_name in requirements
        if importlib.util.find_spec(module_name) is None
    ]

    if not missing:
        return True

    names = ", ".join(package_name for _, package_name in missing)
    print(f"[{label}] Missing dependency: {names}")

    if not auto_install:
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
