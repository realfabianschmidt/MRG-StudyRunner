"""Plugin SDK tests (Phase 5j, docs/archive/architecture-1.0-umbau.md).

Proves the SDK's promise for real: each template is scaffolded with `new`,
then actually validated and booted -- through the exact same code the real
server uses, not a copy of it (see tools/plugin_sdk.py's own docstring).
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import plugin_sdk as sdk  # noqa: E402
from tools import synthetic_lsl_source  # noqa: E402

from study_runner.contracts.manifest import PLUGIN_API_VERSION  # noqa: E402


def _run(func, *args, **kwargs) -> tuple[int, str]:
    """Call an SDK command and capture what it printed, for assertions."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = func(*args, **kwargs)
    return exit_code, buffer.getvalue()


class TemplatesBootThroughTheRealPipelineTests(unittest.TestCase):
    """Each shipped template must scaffold, validate, and actually boot."""

    def test_every_category_template_scaffolds_validates_and_boots(self) -> None:
        for category in sdk.CATEGORY_DEFAULT_KEYS:
            with self.subTest(category=category):
                workspace = Path(tempfile.mkdtemp(prefix="sdk_test_"))
                try:
                    key = f"test_{category}"
                    new_code, _ = _run(sdk.cmd_new, category, key, workspace)
                    self.assertEqual(new_code, 0)

                    plugin_dir = workspace / key
                    validate_code, validate_out = _run(sdk.cmd_validate, plugin_dir)
                    self.assertEqual(validate_code, 0, validate_out)
                    self.assertIn("OK", validate_out)

                    runtime_code, runtime_out = _run(sdk.cmd_check_runtime, plugin_dir)
                    self.assertEqual(runtime_code, 0, runtime_out)
                    self.assertIn("OK", runtime_out)
                finally:
                    shutil.rmtree(workspace, ignore_errors=True)

    def test_raw_templates_carry_their_own_default_key(self) -> None:
        # `new` only ever renames a template; every template must already be
        # a valid, working plugin under its own shipped default key.
        for category, default_key in sdk.CATEGORY_DEFAULT_KEYS.items():
            with self.subTest(category=category):
                manifest = sdk.load_raw_manifest(sdk.TEMPLATES_DIR / category)
                self.assertEqual(manifest["plugin_key"], default_key)
                self.assertEqual(manifest["api_version"], PLUGIN_API_VERSION)


class NewCommandGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="sdk_test_"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def test_rejects_an_unknown_category(self) -> None:
        code, _ = _run(sdk.cmd_new, "not_a_category", "my_key", self.workspace)
        self.assertEqual(code, 2)

    def test_rejects_a_key_that_is_not_snake_case(self) -> None:
        code, _ = _run(sdk.cmd_new, "sensors", "Not-Snake-Case", self.workspace)
        self.assertEqual(code, 2)

    def test_refuses_to_overwrite_an_existing_folder(self) -> None:
        (self.workspace / "my_sensor").mkdir()
        code, _ = _run(sdk.cmd_new, "sensors", "my_sensor", self.workspace)
        self.assertEqual(code, 2)


class SyntheticLslSourceTests(unittest.TestCase):
    """The synthetic source reads a real manifest's own stream contract."""

    def test_pushes_samples_for_the_sensor_template(self) -> None:
        # A fast rate keeps the test quick; push_synthetic_samples sleeps
        # between samples to roughly match whatever rate it is given.
        used_key = synthetic_lsl_source.push_synthetic_samples(
            sdk.TEMPLATES_DIR / "sensors", count=3, rate_hz=1000.0
        )
        self.assertEqual(used_key, "measurements")

    def test_refuses_a_manifest_with_no_streams(self) -> None:
        with self.assertRaisesRegex(ValueError, "no streams"):
            synthetic_lsl_source.push_synthetic_samples(sdk.TEMPLATES_DIR / "outputs")


class SchemaReferenceTests(unittest.TestCase):
    """The checked-in schema file must always match what the generator writes.

    If this fails, someone changed contracts/manifest.py without running
    `python tools/plugin_sdk.py schema --write` -- the fix is to run that
    command, not to hand-edit the schema file.
    """

    def test_checked_in_schema_matches_the_generator(self) -> None:
        expected = json.dumps(sdk.generate_schema(), indent=2) + "\n"
        actual = sdk.SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_schema_command_reports_ok_when_not_stale(self) -> None:
        code, out = _run(sdk.cmd_schema, write=False)
        self.assertEqual(code, 0)
        self.assertIn("OK", out)


if __name__ == "__main__":
    unittest.main()
