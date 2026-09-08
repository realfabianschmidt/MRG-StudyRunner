"""Check a bundle's UI and optional fixture RPC in disposable storage.

server.py --self-check never starts the HTTP server or real hardware.
Packaging CI sets STUDY_RUNNER_SELF_CHECK_PLUGIN to the harmless plugin
fixture staged into its bundle, so discovery alone cannot hide a broken child
process launch. Ordinary self-checks do not initialize any discovered plugin.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
from typing import Iterator


@contextmanager
def _isolated_environment(data_dir: str) -> Iterator[None]:
    overrides = {
        "STUDY_RUNNER_DATA_DIR": data_dir,
        "STUDY_RUNNER_DISABLE_HARDWARE": "1",
        "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        "STUDY_RUNNER_XDF_WORKER": "",
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    fixture_key = os.environ.get("STUDY_RUNNER_SELF_CHECK_PLUGIN", "").strip()
    try:
        with tempfile.TemporaryDirectory(prefix="study-runner-self-check-") as data_dir:
            with _isolated_environment(data_dir):
                return _check_application(fixture_key)
    except Exception as error:
        print(f"[SELF-CHECK] FAILED: {error}")
        return 1


def _check_application(fixture_key: str) -> int:
    from study_runner.backend import create_app
    from study_runner.plugin_framework.plugin_catalog import discover_plugin_catalog

    app = create_app()
    failures: list[str] = []
    static_folder = app.static_folder
    if not static_folder or not os.path.isdir(static_folder):
        failures.append(f"static_folder does not resolve to a directory: {static_folder!r}")
    study_page = os.path.join(static_folder or "", "pages", "study.html")
    if not os.path.isfile(study_page):
        failures.append(f"pages/study.html is missing at {study_page!r}")

    catalog = discover_plugin_catalog()
    print(f"[SELF-CHECK] plugin catalog discovered {len(catalog.entries)} entr(y/ies)")
    for entry in catalog.invalid_entries:
        failures.append(f"invalid plugin {entry.directory}: {'; '.join(entry.errors)}")

    if fixture_key:
        try:
            _check_plugin_process(app, catalog, fixture_key)
        except Exception as error:
            failures.append(f"plugin process check failed: {error}")

    response = app.test_client().get("/")
    try:
        if response.status_code != 200:
            failures.append(f"GET / returned {response.status_code}, expected 200")
    finally:
        response.close()

    if failures:
        print("[SELF-CHECK] FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("[SELF-CHECK] OK: static folder, study page, plugin discovery, and / all resolve.")
    print("[SELF-CHECK] Native recording core and real devices are separate release checks.")
    return 0


def _check_plugin_process(app, catalog, fixture_key: str) -> None:
    from study_runner.plugin_framework.plugin_api import PluginContext
    from study_runner.plugin_framework.plugin_catalog import DEFAULT_PLUGINS_DIRECTORY
    from study_runner.plugin_framework.process_host import PluginProcessRuntime

    entries = [entry for entry in catalog.entries if entry.plugin_key == fixture_key]
    if len(entries) != 1 or entries[0].status != "valid":
        raise RuntimeError(f"expected one valid bundled fixture plugin {fixture_key!r}")
    entry = entries[0]
    runtime = PluginProcessRuntime(entry.manifest, DEFAULT_PLUGINS_DIRECTORY / entry.directory)
    context = PluginContext(
        base_dir=Path(app.config["BASE_DIR"]),
        data_dir=Path(app.config["DATA_DIR"]),
        hardware_config={},
        local_secrets={},
        local_secrets_file=Path(app.config["LOCAL_SECRETS_FILE"]),
    )
    try:
        runtime.initialize(context)
        status = runtime.request("status")
        if not isinstance(status, dict) or status.get("self_check") is not True:
            raise RuntimeError("fixture did not acknowledge the self-check")
        if status.get("pid") == os.getpid() or status.get("pid") != runtime.snapshot()["pid"]:
            raise RuntimeError("fixture RPC did not execute in the supervised child process")
        if Path(str(status.get("data_dir") or "")).resolve() != context.data_dir.resolve():
            raise RuntimeError("fixture did not receive isolated storage")
        if status.get("hardware_disabled") is not True or status.get("background_disabled") is not True:
            raise RuntimeError("fixture did not inherit the isolation switches")
    finally:
        runtime.shutdown()
    stopped = runtime.snapshot()
    if stopped["running"] or stopped["last_exit_code"] != 0:
        raise RuntimeError(f"fixture process did not exit cleanly: {stopped['last_exit_code']}")
    print(f"[SELF-CHECK] plugin {fixture_key}: initialize/status/shutdown RPC completed.")


if __name__ == "__main__":
    sys.exit(main())
