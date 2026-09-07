"""Smoke-test a packaged build without starting the HTTP server.

`server.py --self-check` builds the Flask app the same way a real start would
and asserts the parts a directory-structure rename or a broken PyInstaller
`datas` mapping breaks silently: the bundled frontend is where the app expects
it, the participant page is servable, and plugin discovery does not raise.

This exists because PyInstaller is not built by any CI workflow today (see
docs/architecture-1.0-umbau.md, trap T5) -- the only test that pins the exact
`datas` strings a rename breaks is `release_tools/tests/test_pyinstaller_common.py`,
and that only proves the *spec* is internally consistent, not that a real
bundle starts. This is the fast, no-hardware check that runs against an
actual built bundle in CI.

Always runs with hardware disabled and against an isolated data directory --
this is a build check, not an operator action, and must never touch a real
`saved_results/` or `study_content/`.
"""
from __future__ import annotations

import os
import sys
import tempfile


def main() -> int:
    os.environ["STUDY_RUNNER_DISABLE_HARDWARE"] = "1"
    isolated_data_dir = tempfile.mkdtemp(prefix="study-runner-self-check-")
    os.environ.setdefault("STUDY_RUNNER_DATA_DIR", isolated_data_dir)

    failures: list[str] = []

    try:
        from study_runner.backend import create_app
    except Exception as error:  # noqa: BLE001 - report, do not crash the check
        print(f"[SELF-CHECK] FAILED: could not import the application factory: {error}")
        return 1

    try:
        app = create_app()
    except Exception as error:  # noqa: BLE001
        print(f"[SELF-CHECK] FAILED: create_app() raised: {error}")
        return 1

    static_folder = app.static_folder
    if not static_folder or not os.path.isdir(static_folder):
        failures.append(f"static_folder does not resolve to a directory: {static_folder!r}")

    study_page = os.path.join(static_folder or "", "pages", "study.html")
    if not os.path.isfile(study_page):
        failures.append(f"pages/study.html is missing at {study_page!r}")

    try:
        from study_runner.plugin_framework.plugin_catalog import discover_plugin_catalog

        catalog = discover_plugin_catalog()
        entry_count = len(getattr(catalog, "entries", []) or [])
        print(f"[SELF-CHECK] plugin catalog discovered {entry_count} entr(y/ies)")
    except Exception as error:  # noqa: BLE001
        failures.append(f"discover_plugin_catalog() raised: {error}")

    client = app.test_client()
    response = client.get("/")
    if response.status_code != 200:
        failures.append(f"GET / returned {response.status_code}, expected 200")

    if failures:
        print("[SELF-CHECK] FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("[SELF-CHECK] OK: static folder, study page, plugin discovery, and / all resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
