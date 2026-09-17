"""Package 5d: desc/study_runner XDF header fields from a stream contract."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.stream_contract import (
    apply_stream_contract_desc,
    load_own_stream_contracts,
    stream_contract_desc_fields,
)


_UNKNOWN_STREAM = {
    "source_id": "study_runner.fixture.values",
    "clock_domain": "lsl",
    "timing": {"capture_delay_ns": {"source": "unknown", "min_ns": None, "max_ns": None, "reference": None}},
}


class StreamContractDescFieldsTests(unittest.TestCase):
    def test_unknown_capture_delay_omits_bounds_and_reference(self) -> None:
        fields = stream_contract_desc_fields(_UNKNOWN_STREAM)
        self.assertEqual(
            fields,
            {
                "source_id": "study_runner.fixture.values",
                "clock_domain": "lsl",
                "capture_delay_source": "unknown",
            },
        )
        self.assertNotIn("capture_delay_min_ns", fields)
        self.assertNotIn("capture_delay_max_ns", fields)
        self.assertNotIn("capture_delay_reference", fields)

    def test_measured_capture_delay_includes_bounds_and_reference(self) -> None:
        stream = {
            **_UNKNOWN_STREAM,
            "timing": {
                "capture_delay_ns": {
                    "source": "measured",
                    "min_ns": 12_000_000,
                    "max_ns": 18_000_000,
                    "reference": "loopback-2026-08-14",
                }
            },
        }
        fields = stream_contract_desc_fields(stream)
        self.assertEqual(fields["capture_delay_source"], "measured")
        self.assertEqual(fields["capture_delay_min_ns"], "12000000")
        self.assertEqual(fields["capture_delay_max_ns"], "18000000")
        self.assertEqual(fields["capture_delay_reference"], "loopback-2026-08-14")

    def test_missing_timing_block_is_tolerated_as_unknown(self) -> None:
        fields = stream_contract_desc_fields({"source_id": "x", "clock_domain": "lsl"})
        self.assertEqual(fields["capture_delay_source"], "unknown")


class _FakeDescNode:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.children: dict[str, "_FakeDescNode"] = {}

    def append_child(self, name: str) -> "_FakeDescNode":
        node = _FakeDescNode()
        self.children[name] = node
        return node

    def append_child_value(self, key: str, value: str) -> None:
        self.values[key] = value


class _FakeStreamInfo:
    def __init__(self) -> None:
        self._desc = _FakeDescNode()

    def desc(self) -> _FakeDescNode:
        return self._desc


class ApplyStreamContractDescTests(unittest.TestCase):
    def test_appends_a_study_runner_child_with_flat_fields(self) -> None:
        info = _FakeStreamInfo()
        apply_stream_contract_desc(info, _UNKNOWN_STREAM)
        node = info.desc().children["study_runner"]
        self.assertEqual(node.values["source_id"], "study_runner.fixture.values")
        self.assertEqual(node.values["capture_delay_source"], "unknown")


class LoadOwnStreamContractsTests(unittest.TestCase):
    def test_loads_and_normalizes_a_real_manifest_by_key(self) -> None:
        adapter_file = str(
            PROJECT_ROOT / "study_runner" / "plugins" / "sensors" / "brainbit" / "adapter.py"
        )
        contracts = load_own_stream_contracts(adapter_file)
        self.assertEqual(
            set(contracts),
            {"eeg", "bands", "mental", "quality", "battery", "diagnostics"},
        )
        self.assertEqual(contracts["eeg"]["timing"]["capture_delay_ns"]["source"], "unknown")

    def test_raises_a_clear_error_when_no_manifest_sits_beside_the_file(self) -> None:
        with self.assertRaises(OSError):
            load_own_stream_contracts(str(PROJECT_ROOT / "study_runner" / "version.py"))


if __name__ == "__main__":
    unittest.main()
