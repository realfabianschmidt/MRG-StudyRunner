"""Event-driven streams may be empty only when their contract says so."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.data_core.host.recording_contract import build_recording_contract  # noqa: E402
from study_runner.data_core.host.recording_quality import scientific_source_checks  # noqa: E402
from study_runner.data_core.host.xdf import StreamInspection, XdfArtifactInspection  # noqa: E402
from study_runner.plugins.sensors.brainbit import adapter as brainbit_adapter  # noqa: E402
from study_runner.plugin_framework.registry import get_plugin_manifest  # noqa: E402


def _stream(source_id: str, *, rate: float, samples: int) -> StreamInspection:
    return StreamInspection(
        origin_id=f"fixture:{source_id}",
        name=source_id,
        stream_type="FIXTURE",
        source_id=source_id,
        nominal_srate=rate,
        channel_count=1,
        sample_count=samples,
        first_timestamp=1.0 if samples else None,
        last_timestamp=2.0 if samples else None,
        sample_hash="samples",
        timestamp_hash="timestamps",
        clock_offsets_hash="offsets",
        metadata_hash="metadata",
    )


def _checks(state_stream: dict) -> tuple[set[str], dict]:
    manifest = {
        "plugin_key": "fixture",
        "version": "1.0.0",
        "capabilities": ["study_sensor", "recording_source"],
        "streams": [
            {"key": "signal", "source_id": "fixture.signal", "nominal_rate_hz": 0,
             "channels": ["value"], "channel_units": ["unit"]},
            {"key": "state", "source_id": "fixture.state", "nominal_rate_hz": 0,
             "channels": ["value"], "channel_units": ["unit"], **state_stream},
        ],
    }
    backup = {
        "rate_hz": 1.0,
        "artifact_role": "derived_backup",
        "resampling_strategy": "latest_cached_at_slowest_projection_grid; stale_to_nan",
        "quality_channels": ["valid", "sample_age_ms", "sequence", "status"],
        "channel_names": [],
        "source_rates_hz": {},
        "active_plugins": ["fixture"],
        "projections": [{"plugin_key": "fixture", "rate_hz": 1.0, "channels": []}],
    }
    contract = build_recording_contract(["fixture"], ["fixture"], {"fixture": manifest}, backup)
    plan = {
        "recording_plugins": ["fixture"],
        "required_source_keys": ["fixture"],
        "recording_contract": contract,
        "backup": None,
    }
    artifact = XdfArtifactInspection(
        path=Path("fixture.xdf"),
        source_key="fixture",
        readable=True,
        file_sha256="hash",
        streams=(
            _stream("fixture.signal", rate=0.0, samples=5),
            _stream("fixture.state", rate=0.0, samples=0),
        ),
        error=None,
    )
    issues, metrics = scientific_source_checks(plan, [artifact])
    state_metric = next(m for m in metrics["declared_streams"] if m["source_id"] == "fixture.state")
    return {issue.code for issue in issues}, state_metric


class EmptyDeclaredStreamTests(unittest.TestCase):
    def test_empty_stream_without_opt_in_still_fails(self) -> None:
        codes, metric = _checks({})
        self.assertIn("empty_declared_stream", codes)
        self.assertFalse(metric["empty_allowed"])

    def test_empty_stream_marked_may_be_empty_is_accepted_and_reported(self) -> None:
        codes, metric = _checks({"may_be_empty": True})
        self.assertNotIn("empty_declared_stream", codes)
        self.assertTrue(metric["empty_allowed"])
        self.assertEqual(metric["sample_count"], 0)

    def test_brainbit_marks_exactly_its_pre_recording_state_streams(self) -> None:
        runtime = brainbit_adapter._actual_stream_contracts(
            ("O1", "O2", "T3", "T4"), nominal_rate_hz=250.0, derived_rate_hz=25.0, derived_enabled=True
        )
        manifest = get_plugin_manifest("brainbit")
        for streams in (runtime, manifest["streams"]):
            self.assertEqual(
                {stream["key"] for stream in streams if stream.get("may_be_empty") is True},
                {"quality", "battery"},
            )


if __name__ == "__main__":
    unittest.main()
