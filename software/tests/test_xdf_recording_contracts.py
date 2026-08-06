from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.recording.errors import XdfBackendUnavailableError
from study_runner.recording.xdf import (
    PyXdfInspector,
    PythonRecoveryJournalBackend,
    StreamInspection,
    UnavailableCanonicalXdfBackend,
    XdfArtifactInspection,
    validate_merge_parity,
    validate_sources,
)


def stream(origin: str, *, sample_hash: str = "samples") -> StreamInspection:
    return StreamInspection(
        origin_id=origin,
        name="EEG",
        stream_type="EEG",
        source_id="brainbit-001",
        nominal_srate=250.0,
        channel_count=4,
        sample_count=500,
        first_timestamp=100.0,
        last_timestamp=101.996,
        sample_hash=sample_hash,
        timestamp_hash="timestamps",
        clock_offsets_hash="offsets",
        metadata_hash="metadata",
        stream_id=f"stream-{origin}",
    )


class XdfRecordingContractTests(unittest.TestCase):
    def test_merge_parity_accepts_exact_native_stream_and_rejects_changed_data(self) -> None:
        source_stream = stream("brainbit:part-0001:0")
        source = XdfArtifactInspection(
            path=Path("part-0001.xdf"),
            source_key="brainbit",
            readable=True,
            file_sha256="source-file",
            streams=(source_stream,),
        )
        merged = XdfArtifactInspection(
            path=Path("session.xdf"),
            source_key="merged",
            readable=True,
            file_sha256="merged-file",
            streams=(source_stream,),
        )

        self.assertTrue(validate_merge_parity((source,), merged).ok)

        corrupt = replace(merged, streams=(replace(source_stream, sample_hash="changed"),))
        report = validate_merge_parity((source,), corrupt)
        self.assertFalse(report.ok)
        self.assertIn("parity_sample_hash", {issue.code for issue in report.issues})

    def test_required_source_validation_never_silently_completes(self) -> None:
        optional = XdfArtifactInspection(
            path=Path("radar.xdf"),
            source_key="radar",
            readable=True,
            file_sha256="hash",
            streams=(stream("radar:0"),),
        )

        report = validate_sources((optional,), required_source_keys=("brainbit", "radar"))
        self.assertFalse(report.ok)
        self.assertIn("missing_required_source", {issue.code for issue in report.issues})

    def test_python_fallback_has_distinct_suffix_and_is_not_canonical_xdf(self) -> None:
        backend = PythonRecoveryJournalBackend()
        self.assertFalse(backend.status().canonical_xdf)
        with tempfile.TemporaryDirectory() as tmp:
            requested = Path(tmp) / "part-0001.xdf"
            with backend.open_for_requested_xdf(requested, metadata={"plugin": "radar"}) as writer:
                writer.append(
                    stream_id="vitals",
                    source_timestamp=1.0,
                    received_monotonic=2.0,
                    sequence=3,
                    values=(72.0, float("nan")),
                )

            self.assertFalse(requested.exists())
            journal = Path(tmp) / "part-0001.recovery.jsonl"
            self.assertTrue(journal.is_file())
            lines = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
            self.assertFalse(lines[0]["canonical_xdf"])
            self.assertEqual(lines[-1]["record_type"], "footer")

    def test_unavailable_backend_refuses_fake_merge(self) -> None:
        backend = UnavailableCanonicalXdfBackend("native writer is not packaged")
        self.assertFalse(backend.status().available)
        with self.assertRaises(XdfBackendUnavailableError):
            backend.merge([], Path("session.xdf"), command_id="merge-1")

    def test_backup_invalid_values_are_checked_per_projection_prefix(self) -> None:
        labels = (
            "sensor_a.vitals.value",
            "sensor_a.vitals.valid",
            "sensor_a.vitals.sample_age_ms",
            "sensor_a.vitals.sequence",
            "sensor_a.vitals.status",
            "sensor_b.vitals.value",
            "sensor_b.vitals.valid",
            "sensor_b.vitals.sample_age_ms",
            "sensor_b.vitals.sequence",
            "sensor_b.vitals.status",
        )
        invalid_a_valid_b = [
            float("nan"), 0.0, 3000.0, 7.0, 2.0,
            72.0, 1.0, 20.0, 8.0, 1.0,
        ]
        invalid_a_with_own_value = [
            12.0, 0.0, 3000.0, 8.0, 2.0,
            73.0, 1.0, 10.0, 9.0, 1.0,
        ]

        clean = PyXdfInspector._inspect_stream(
            _backup_stream(labels, [invalid_a_valid_b]),
            generated_origin="derived_backup:backup.xdf:0",
            require_embedded_origin=False,
        )
        mixed = PyXdfInspector._inspect_stream(
            _backup_stream(labels, [invalid_a_valid_b, invalid_a_with_own_value]),
            generated_origin="derived_backup:backup.xdf:0",
            require_embedded_origin=False,
        )

        self.assertEqual(clean.invalid_rows_with_values, 0)
        self.assertEqual(mixed.invalid_rows_with_values, 1)


def _backup_stream(labels: tuple[str, ...], rows: list[list[float]]) -> dict:
    return {
        "info": {
            "name": ["StudyRunnerBackup"],
            "type": ["DerivedBackup"],
            "source_id": ["study_runner.derived_backup"],
            "nominal_srate": ["1"],
            "channel_count": [str(len(labels))],
            "channel": [{"label": [label]} for label in labels],
        },
        "time_series": rows,
        "time_stamps": [float(index + 1) for index in range(len(rows))],
        "clock_times": [],
        "clock_values": [],
    }


if __name__ == "__main__":
    unittest.main()
