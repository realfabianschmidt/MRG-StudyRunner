"""Every plugin follows the same clock rules (clock_core.contract).

A plugin delivers values and declares where its times come from. It never
owns an LSL outlet or a clock mapping: samples go through SensorStreams, and
a reconstructed source timeline uses the clock core's helpers.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.sensor_contract import TIMESTAMP_SOURCES  # noqa: E402

PLUGINS_ROOT = PROJECT_ROOT / "study_runner" / "plugins"
CLOCK_READS = {("time", "time"), ("time", "monotonic"), ("time", "perf_counter")}


def _plugin_dirs() -> list[Path]:
    return sorted(path.parent for path in PLUGINS_ROOT.glob("*/*/manifest.json"))


def _runtime_sources(plugin_dir: Path) -> list[Path]:
    # Standalone bench tools under tools/ are not part of the recording path.
    return [
        path
        for path in sorted(plugin_dir.rglob("*.py"))
        if "tools" not in path.relative_to(plugin_dir).parts and "__pycache__" not in path.parts
    ]


def _manifest(plugin_dir: Path) -> dict:
    return json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8"))


class PluginClockContractTests(unittest.TestCase):
    def test_plugin_discovery_finds_the_sensors(self) -> None:
        names = {path.name for path in _plugin_dirs()}
        self.assertTrue({"brainbit", "am_hub"} <= names)

    def test_every_stream_declares_its_timestamp_source(self) -> None:
        offenders = [
            f"{plugin_dir.name}.{stream.get('key')}"
            for plugin_dir in _plugin_dirs()
            for stream in _manifest(plugin_dir).get("streams") or []
            if (stream.get("timing") or {}).get("timestamp_source") not in TIMESTAMP_SOURCES
        ]
        self.assertEqual(offenders, [])

    def test_no_plugin_owns_an_lsl_outlet_or_clock(self) -> None:
        offenders = []
        for plugin_dir in _plugin_dirs():
            for path in _runtime_sources(plugin_dir):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    elif isinstance(node, ast.Attribute) and node.attr in {"local_clock", "StreamOutlet"}:
                        names = [node.attr]
                    if any(name == "pylsl" or name.startswith("pylsl.") or name in {"local_clock", "StreamOutlet"} for name in names):
                        offenders.append(f"{path.relative_to(PLUGINS_ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [], "publish through SensorStreams; read the LSL clock with streams.now()")

    def test_published_timestamps_never_come_straight_from_a_host_clock(self) -> None:
        offenders = []
        for plugin_dir in _plugin_dirs():
            for path in _runtime_sources(plugin_dir):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                for call in ast.walk(tree):
                    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                        continue
                    if call.func.attr not in {"push", "push_chunk"} or len(call.args) < 3:
                        continue
                    for inner in ast.walk(call.args[2]):
                        if (
                            isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Attribute)
                            and isinstance(inner.func.value, ast.Name)
                            and (inner.func.value.id, inner.func.attr) in CLOCK_READS
                        ):
                            offenders.append(f"{path.relative_to(PLUGINS_ROOT)}:{call.lineno}")
        self.assertEqual(offenders, [], "a sample timestamp is streams.now() or a mapped source time")

    def test_reconstructed_timelines_use_the_clock_core(self) -> None:
        offenders = []
        for plugin_dir in _plugin_dirs():
            sources = {
                (stream.get("timing") or {}).get("timestamp_source")
                for stream in _manifest(plugin_dir).get("streams") or []
            }
            if "host_callback_reconstructed" not in sources:
                continue
            text = "\n".join(path.read_text(encoding="utf-8") for path in _runtime_sources(plugin_dir))
            for helper in ("callback_batch_start", "SourceClock", "SourceToLsl"):
                if helper not in text:
                    offenders.append(f"{plugin_dir.name} does not use clock_core.producer.{helper}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
