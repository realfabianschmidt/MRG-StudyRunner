"""Session-wide pytest hygiene for the plugin process-host singletons.

`study_runner.plugin_framework.process_host` keeps one `PluginProcessRuntime`
per plugin key in a process-wide registry, reused across every `create_app()`
call in this test session. A test that does not disable hardware can cause a
real driver.py subprocess (and its stdout/stderr reader threads) to start;
nothing stops it when that test ends. A later test's own
`tempfile.TemporaryDirectory()` can then be deleted while that lingering
thread is still writing a plugin log into it, which is a Windows file-lock
race (`rmtree` fails with "directory not empty"), not a real product bug.
Stopping any live subprocess after every test removes the lingering thread
without touching the registry identity that `PLUGINS_BY_KEY`'s dispatch
closures rely on.

A second, related leak: `PluginHealthPollService` (created fresh per
`create_app()` call, one per test) only stops its `ThreadPoolExecutor` via a
`weakref.finalize` callback that fires when the service itself is garbage
collected. A Flask `app` is not reference-cycle-free, so CPython's
refcounting alone will not always collect it the instant a test function
returns -- it can wait for the next scheduled cyclic-GC pass, leaving that
executor's worker threads alive in the meantime. Forcing a collection pass
after every test makes that cleanup deterministic instead of "usually".

Third: the remembered data folders live in the user's own settings folder
(%APPDATA%, ~/Library/Application Support). Tests use a temporary one, so they
never read or write the real list.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework.process_host import shutdown_process_plugins


@pytest.fixture(autouse=True, scope="session")
def _private_user_config_dir(tmp_path_factory):
    previous = os.environ.get("STUDY_RUNNER_USER_CONFIG_DIR")
    os.environ["STUDY_RUNNER_USER_CONFIG_DIR"] = str(tmp_path_factory.mktemp("user-config"))
    yield
    if previous is None:
        os.environ.pop("STUDY_RUNNER_USER_CONFIG_DIR", None)
    else:
        os.environ["STUDY_RUNNER_USER_CONFIG_DIR"] = previous


@pytest.fixture(autouse=True)
def _stop_plugin_subprocesses_between_tests():
    yield
    shutdown_process_plugins()
    gc.collect()
