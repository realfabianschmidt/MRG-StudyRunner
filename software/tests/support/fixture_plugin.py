"""Shared setup for tests that discover a synthetic plugin via a temp directory.

A v4 plugin's handlers run in a spawned subprocess (``driver.py``), which has
no access to this process's ``sys.path`` or monkeypatches -- only its
environment survives the spawn (``env = os.environ.copy()`` in
``plugin_framework/process_host.py``). This mixin registers the temp package
with ``plugin_layout``'s test-only environment seam so a real subprocess
can resolve and import the fixture plugin exactly like a real one, instead of
faking the v3 in-process import path that Phase 3.1
(docs/architecture-1.0-umbau.md) removed.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import uuid

from study_runner.plugin_framework.plugin_layout import (
    TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR,
    TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DRIVER_PY_TEMPLATE = (
    "from study_runner.plugin_framework.driver_runtime import run_plugin_driver\n"
    "\n\n"
    "if __name__ == \"__main__\":\n"
    "    raise SystemExit(run_plugin_driver({plugin_key!r}))\n"
)


def write_driver_py(plugin_dir: Path, plugin_key: str) -> None:
    """The one-line shim every v4 plugin needs; identical for all of them --
    see tests/fixtures/packaging_probe/driver.py for the real, shipped copy.
    """
    (plugin_dir / "driver.py").write_text(
        DRIVER_PY_TEMPLATE.format(plugin_key=plugin_key), encoding="utf-8"
    )


class FixturePluginRootMixin:
    """Give a test its own temp package dir, wired for real v4 plugin discovery.

    Sets ``self.root`` (temp parent dir), ``self.package_name`` (unique per
    test run) and ``self.package_dir`` (``root/package_name``, an
    ``__init__.py``-marked package whose subdirectories are plugin bundles --
    write ``<package_dir>/<plugin_key>/{manifest.json,plugin.py,driver.py}``,
    then discover with
    ``discover_plugin_catalog(self.package_dir, package_name=self.package_name)``).

    Combine with ``unittest.TestCase`` and put this mixin first:
    ``class MyTests(FixturePluginRootMixin, unittest.TestCase): ...``
    """

    fixture_group_name = "fixture-plugin"

    def setUp(self) -> None:
        super().setUp()
        parent = PROJECT_ROOT / ".tmp" / self.fixture_group_name
        parent.mkdir(parents=True, exist_ok=True)
        self.root = parent / uuid.uuid4().hex
        self.root.mkdir()
        self.package_name = f"fixture_plugins_{self.root.name}"
        self.package_dir = self.root / self.package_name
        self.package_dir.mkdir()
        (self.package_dir / "__init__.py").write_text("", encoding="utf-8")
        sys.path.insert(0, str(self.root))

        self._previous_environ = {
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
            TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR: os.environ.get(TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR),
            TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR: os.environ.get(TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR),
        }
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        os.environ["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(self.root), existing_pythonpath) if item
        )
        os.environ[TEST_EXTRA_PLUGIN_ROOT_PATH_ENV_VAR] = str(self.package_dir)
        os.environ[TEST_EXTRA_PLUGIN_ROOT_PACKAGE_ENV_VAR] = self.package_name

    def tearDown(self) -> None:
        for name, value in self._previous_environ.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

        root_text = str(self.root)
        if root_text in sys.path:
            sys.path.remove(root_text)
        for module_name in list(sys.modules):
            if module_name == self.package_name or module_name.startswith(f"{self.package_name}."):
                sys.modules.pop(module_name, None)
        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()
