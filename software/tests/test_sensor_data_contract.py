"""Every sensor plugin -- and the sensor template -- follows the one data contract.

Raw: each real sample is one LSL sample, published only through
``plugin_framework/sensor_streams.py`` (no plugin builds an outlet itself).
Backup: the manifest's ``backup_projection`` at the core's fixed 1 Hz.
Live: the manifest's ``live_view`` series (2 Hz, 60 s), fed by the same pushes.
Every stream says where its timestamps come from.

A sensor not yet on the contract is listed in NOT_YET_ON_CONTRACT. The list
may only shrink: a listed plugin that already passes fails this test until it
is removed (the same ratchet as test_import_boundaries.py).
"""
from __future__ import annotations

import ast
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOFTWARE_ROOT = PROJECT_ROOT / "software"
for root in (PROJECT_ROOT, SOFTWARE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from tools import plugin_sdk as sdk  # noqa: E402

from study_runner.contracts.manifest import validate_and_normalize_manifest  # noqa: E402
from study_runner.contracts.sensor_contract import (  # noqa: E402
    BACKUP_PROJECTION_RATE_HZ,
    DEFAULT_BACKUP_STALE_AFTER_MS,
    TIMESTAMP_SOURCES,
)

SENSORS_ROOT = SOFTWARE_ROOT / "study_runner" / "plugins" / "sensors"
TEMPLATE = sdk.TEMPLATES_DIR / "sensors"
NOT_YET_ON_CONTRACT = {"am_hub", "brainbit", "camera_emotion", "mr60_mini_radar"}
# Names only the shared publishing path may use.
FORBIDDEN_NAMES = {"StreamOutlet", "StreamInfo"}
FORBIDDEN_CALLS = {"push_sample"}


def _sensor_folders() -> list[Path]:
    return sorted(
        folder for folder in SENSORS_ROOT.iterdir()
        if folder.is_dir() and (folder / "manifest.json").is_file()
    )


def _manifest(folder: Path) -> dict:
    raw = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    return validate_and_normalize_manifest(raw, directory_name=folder.name)


def _contract_problems(folder: Path) -> list[str]:
    manifest = _manifest(folder)
    capabilities = manifest["capability_config"]
    problems: list[str] = []
    if "study_sensor" in capabilities and "recording_source" in capabilities:
        backup = capabilities.get("backup_projection") or {}
        if backup.get("rate_hz") != BACKUP_PROJECTION_RATE_HZ:
            problems.append(f"backup_projection.rate_hz is {backup.get('rate_hz')}")
        if "live_view" not in capabilities:
            problems.append("no live_view")
    for stream in manifest["streams"]:
        if (stream.get("timing") or {}).get("timestamp_source") not in TIMESTAMP_SOURCES:
            problems.append(f"stream {stream['key']!r} has no timing.timestamp_source")
    problems.extend(_publishing_problems(folder))
    return problems


def _publishing_problems(folder: Path) -> list[str]:
    problems: list[str] = []
    uses_helper = False
    for path in sorted(folder.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8-sig")
        uses_helper = uses_helper or "SensorStreams.for_plugin(" in source
        relative = path.relative_to(folder).as_posix()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "pylsl" for alias in node.names):
                problems.append(f"{relative}:{node.lineno} imports pylsl")
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pylsl":
                problems.append(f"{relative}:{node.lineno} imports pylsl")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                problems.append(f"{relative}:{node.lineno} uses {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES | FORBIDDEN_CALLS:
                problems.append(f"{relative}:{node.lineno} uses .{node.attr}")
    if not uses_helper:
        problems.append("no SensorStreams.for_plugin(...) -- samples must go through the shared helper")
    return problems


class SensorDataContractTests(unittest.TestCase):
    def test_every_sensor_plugin_follows_the_contract(self) -> None:
        for folder in _sensor_folders():
            if folder.name in NOT_YET_ON_CONTRACT:
                continue
            with self.subTest(plugin=folder.name):
                self.assertEqual(_contract_problems(folder), [])

    def test_the_not_yet_list_only_names_plugins_that_still_need_it(self) -> None:
        folders = {folder.name for folder in _sensor_folders()}
        self.assertLessEqual(NOT_YET_ON_CONTRACT, folders, "remove plugins that no longer exist")
        for name in sorted(NOT_YET_ON_CONTRACT):
            with self.subTest(plugin=name):
                self.assertNotEqual(
                    _contract_problems(SENSORS_ROOT / name),
                    [],
                    f"{name} follows the contract now: remove it from NOT_YET_ON_CONTRACT",
                )

    def test_the_sensor_template_follows_the_contract(self) -> None:
        self.assertEqual(_contract_problems(TEMPLATE), [])
        backup = _manifest(TEMPLATE)["capability_config"]["backup_projection"]
        self.assertEqual(backup["stale_after_ms"], DEFAULT_BACKUP_STALE_AFTER_MS)
        self.assertNotIn("rate_hz", json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))["capabilities"]["backup_projection"])

    def test_a_new_sensor_reports_its_live_view_and_stream_health(self) -> None:
        workspace = Path(tempfile.mkdtemp(prefix="sdk_contract_"))
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(sdk.cmd_new("sensors", "contract_probe", workspace), 0)
            output = io.StringIO()
            with redirect_stdout(output):
                code = sdk.cmd_check_runtime(workspace / "contract_probe")
            self.assertEqual(code, 0, output.getvalue())
            status = json.loads(output.getvalue().split("\n", 1)[1])
            self.assertIn("value", status["live"]["series"])
            self.assertFalse(status["stream_health"]["failed"])
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
