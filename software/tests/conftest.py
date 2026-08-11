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
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework.process_host import shutdown_process_plugins


@pytest.fixture(autouse=True)
def _stop_plugin_subprocesses_between_tests():
    yield
    shutdown_process_plugins()
