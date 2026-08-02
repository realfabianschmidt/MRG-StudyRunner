from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import unittest
import uuid
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.recording_worker.core import NativeXdfCore, NativeXdfError
from study_runner.backend.recording.backup import (
    BackupSampler,
    STATUS_DEGRADED,
    STATUS_STALE,
    projections_from_manifest,
)
from study_runner.recording_worker.lsl_recording import (
    BackupRecorder,
    LslSourceRecorder,
    ProjectionCache,
    StreamSpec,
)
from study_runner.recording_worker.runtime import RecordingWorkerRuntime, sha256_file
from study_runner.backend.recording.worker_protocol import (
    WorkerCommand,
    WorkerEndpointState,
    WorkerStateStore,
)


def _stream_header(name: str, source_id: str, rate: float, channel_format: str) -> str:
    return (
        '<?xml version="1.0"?><info>'
        f"<name>{name}</name><type>TEST</type><channel_count>1</channel_count>"
        f"<nominal_srate>{rate}</nominal_srate><channel_format>{channel_format}</channel_format>"
        f"<source_id>{source_id}</source_id><version>1.100000</version>"
        f"<created_at>1</created_at><uid>{source_id}</uid><session_id>smoke</session_id>"
        "<hostname>localhost</hostname><desc><channels><channel>"
        "<label>value</label><unit>arbitrary</unit>"
        "</channel></channels></desc></info>"
    )


def _footer(sample_count: int, first: float, last: float) -> str:
    return (
        '<?xml version="1.0"?><info>'
        f"<first_timestamp>{first}</first_timestamp><last_timestamp>{last}</last_timestamp>"
        f"<sample_count>{sample_count}</sample_count><clock_offsets/></info>"
    )


@unittest.skipUnless(os.environ.get("STUDY_RUNNER_XDF_CORE_TEST"), "native core path not provided")
class NativeCoreSmokeTests(unittest.TestCase):
    def test_all_xdf_base_types_round_trip_through_pyxdf(self) -> None:
        import pyxdf

        core = NativeXdfCore(Path(os.environ["STUDY_RUNNER_XDF_CORE_TEST"]))
        root = REPOSITORY_ROOT / ".tmp" / f"native-types-smoke-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        path = root / "all-types.xdf"
        writer = core.create_writer(path)
        specifications = (
            ("int8", [-128, 127]),
            ("int16", [-32768, 32767]),
            ("int32", [-(2**31), (2**31) - 1]),
            ("int64", [-(2**40), 2**40]),
            ("float32", [1.25, -2.5]),
            ("double64", [math.pi, -1.0e100]),
            ("string", ["Grüße", "marker|event_id=base-type"]),
        )
        try:
            timestamps = [1000.0, 1000.5]
            for index, (channel_format, values) in enumerate(specifications, start=1):
                source_id = f"study_runner.base_type.{channel_format}"
                writer.write_stream_header(
                    index,
                    _stream_header(channel_format, source_id, 2.0, channel_format),
                )
                writer.write_samples(
                    index,
                    timestamps,
                    [[value] for value in values],
                    channel_format=channel_format,
                    channel_count=1,
                )
                writer.write_stream_footer(
                    index,
                    _footer(len(values), timestamps[0], timestamps[-1]),
                )
            writer.close(durable=True)
        finally:
            writer.destroy()

        try:
            streams, _header = pyxdf.load_xdf(
                str(path),
                synchronize_clocks=False,
                handle_clock_resets=False,
                dejitter_timestamps=False,
                verbose=False,
            )
            by_source_id = {
                stream["info"]["source_id"][0]: stream
                for stream in streams
            }
            self.assertEqual(len(by_source_id), len(specifications))
            for channel_format, expected in specifications:
                stream = by_source_id[f"study_runner.base_type.{channel_format}"]
                rows = stream["time_series"]
                if hasattr(rows, "tolist"):
                    rows = rows.tolist()
                actual = [row[0] for row in rows]
                if channel_format in {"float32", "double64"}:
                    for value, wanted in zip(actual, expected, strict=True):
                        self.assertAlmostEqual(float(value), float(wanted), places=5)
                else:
                    self.assertEqual(actual, expected)
        finally:
            shutil.rmtree(root)

    def test_truncated_last_chunk_is_preserved_but_never_validated_as_complete(self) -> None:
        from study_runner.backend.recording.xdf import PyXdfInspector, validate_sources

        core = NativeXdfCore(Path(os.environ["STUDY_RUNNER_XDF_CORE_TEST"]))
        root = REPOSITORY_ROOT / ".tmp" / f"native-truncated-smoke-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        intact = root / "intact.xdf"
        truncated = root / "truncated.xdf"
        writer = core.create_writer(intact)
        try:
            writer.write_stream_header(
                1,
                _stream_header("truncation", "study_runner.truncation", 2.0, "float32"),
            )
            writer.write_samples(
                1,
                [1000.0, 1000.5],
                [[1.0], [2.0]],
                channel_format="float32",
                channel_count=1,
            )
            writer.write_stream_footer(1, _footer(2, 1000.0, 1000.5))
            writer.close(durable=True)
        finally:
            writer.destroy()

        try:
            payload = intact.read_bytes()
            self.assertGreater(len(payload), 32)
            intact_inspection = PyXdfInspector().inspect(intact, source_key="required_sensor")
            intact_report = validate_sources(
                [intact_inspection],
                required_source_keys=["required_sensor"],
            )
            self.assertTrue(
                intact_report.ok,
                {issue.code for issue in intact_report.issues},
            )
            truncated.write_bytes(payload[:-8])
            inspection = PyXdfInspector().inspect(truncated, source_key="required_sensor")
            report = validate_sources(
                [inspection],
                required_source_keys=["required_sensor"],
            )
            self.assertFalse(report.ok)
            issue_codes = {issue.code for issue in report.issues}
            self.assertTrue(
                issue_codes
                & {
                    "unreadable_source",
                    "source_footer_missing",
                    "source_footer_sample_count_missing",
                    "source_footer_sample_count_mismatch",
                },
                issue_codes,
            )
            self.assertTrue(truncated.is_file(), "the recovery fragment must remain on disk")
        finally:
            shutil.rmtree(root)

    def test_native_rates_payloads_provenance_abort_and_exclusive_merge(self) -> None:
        import pyxdf

        core = NativeXdfCore(Path(os.environ["STUDY_RUNNER_XDF_CORE_TEST"]))
        root = REPOSITORY_ROOT / ".tmp" / f"native-core-smoke-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        try:
            source_specs = (
                ("sensor250", 250.0, "float32", [1.25, 2.5, 3.75]),
                ("sensor10", 10.0, "float32", [10.0, 20.0]),
                ("derived_backup", 1.0, "double64", [100.0, float("nan")]),
            )
            sources: list[tuple[str, Path]] = []
            for index, (key, rate, channel_format, values) in enumerate(source_specs):
                path = root / f"source-{index}.xdf"
                writer = core.create_writer(path)
                timestamps = [1000.0 + offset / rate for offset in range(len(values))]
                try:
                    writer.write_stream_header(
                        77,
                        _stream_header(key, f"study_runner.{key}", rate, channel_format),
                    )
                    writer.write_samples(
                        77,
                        timestamps,
                        [[value] for value in values],
                        channel_format=channel_format,
                        channel_count=1,
                    )
                    writer.write_clock_offset(77, 1001.0, 0.001 * (index + 1))
                    writer.write_stream_footer(77, _footer(len(values), timestamps[0], timestamps[-1]))
                    writer.close(durable=True)
                finally:
                    writer.destroy()
                sources.append((key, path))

            partial = root / "partial.xdf"
            partial_writer = core.create_writer(partial)
            partial_writer.write_stream_header(
                1,
                _stream_header("partial", "study_runner.partial", 1.0, "float32"),
            )
            partial_writer.abort(durable=True)
            partial_writer.destroy()
            self.assertGreater(partial.stat().st_size, 4)

            merged = root / "session.xdf"
            report = core.merge(sources, merged)
            self.assertEqual(report["source_count"], 3)
            self.assertEqual(report["stream_count"], 3)
            before = hashlib.sha256(merged.read_bytes()).hexdigest()
            with self.assertRaises(NativeXdfError):
                core.merge(sources, merged)
            self.assertEqual(hashlib.sha256(merged.read_bytes()).hexdigest(), before)

            streams, _header = pyxdf.load_xdf(
                str(merged),
                synchronize_clocks=False,
                handle_clock_resets=False,
                dejitter_timestamps=False,
                verbose=False,
            )
            self.assertEqual([float(stream["info"]["nominal_srate"][0]) for stream in streams], [250, 10, 1])
            for index, stream in enumerate(streams):
                info_text = json.dumps(stream["info"], ensure_ascii=False)
                expected_key = source_specs[index][0]
                self.assertIn(
                    f"{expected_key}:source-{index}.xdf:0",
                    info_text,
                )
                self.assertEqual(len(stream["clock_times"]), 1)
        finally:
            shutil.rmtree(root)

    def test_synthetic_lsl_source_backup_freeze_and_worker_merge(self) -> None:
        import pylsl
        import pyxdf

        root = REPOSITORY_ROOT / ".tmp" / f"worker-smoke-{uuid.uuid4().hex}"
        root.mkdir(parents=True)
        runtime = None
        try:
            (root / "logs").mkdir()
            endpoint = WorkerEndpointState.create(
                session_id="synthetic-session",
                port=32123,
                generation=1,
            )
            state_file = root / "recording-worker-state.json"
            WorkerStateStore(state_file).save(endpoint)
            runtime = RecordingWorkerRuntime(
                session_id=endpoint.session_id,
                session_dir=root,
                state_file=state_file,
                generation=endpoint.generation,
                token=endpoint.token,
                core_path=Path(os.environ["STUDY_RUNNER_XDF_CORE_TEST"]),
                lease_seconds=60,
            )

            info = pylsl.StreamInfo(
                "StudyRunnerSynthetic",
                "TEST",
                1,
                10.0,
                pylsl.cf_float32,
                "study_runner.synthetic.values",
            )
            channel = info.desc().append_child("channels").append_child("channel")
            channel.append_child_value("label", "value")
            channel.append_child_value("unit", "arbitrary")
            outlet = pylsl.StreamOutlet(info)
            source_path = root / "raw" / "plugins" / "synthetic" / "part-0001.xdf"
            source_command = WorkerCommand(
                "start_recording_source",
                {
                    "session_id": endpoint.session_id,
                    "plugin_key": "synthetic",
                    "target_path": str(source_path),
                    "streams": [
                        {
                            "key": "values",
                            "source_id": "study_runner.synthetic.values",
                            "type": "TEST",
                            "nominal_rate_hz": 10,
                            "channel_format": "float32",
                            "channels": ["value"],
                            "channel_units": ["arbitrary"],
                        }
                    ],
                },
                command_id="start-synthetic",
            )
            runtime.start_recording_source(source_command)
            self.assertTrue(outlet.wait_for_consumers(5.0))

            backup_path = root / "raw" / "backup" / "slowest-grid_1hz.xdf"
            backup_channels = [
                "synthetic.values.value",
                "synthetic.values.valid",
                "synthetic.values.sample_age_ms",
                "synthetic.values.sequence",
                "synthetic.values.status",
            ]
            runtime.start_backup_projection(
                WorkerCommand(
                    "start_backup_projection",
                    {
                        "session_id": endpoint.session_id,
                        "target_path": str(backup_path),
                        "generation": 1,
                        "rate_hz": 1,
                        "grid_anchor_epoch": time.time(),
                        "artifact_role": "derived_backup",
                        "resampling_strategy": "latest_cached_at_slowest_projection_grid; stale_to_nan",
                        "active_plugins": ["synthetic"],
                        "source_rates_hz": {"synthetic.values": 10},
                        "quality_channels": ["valid", "sample_age_ms", "sequence", "status"],
                        "channel_names": backup_channels,
                        "projections": [
                            {
                                "plugin_key": "synthetic",
                                "rate_hz": 1,
                                "stale_after_ms": 2500,
                                "channels": [
                                    {"output": "value", "stream": "values", "channel": "value"}
                                ],
                            }
                        ],
                    },
                    command_id="start-backup",
                )
            )
            start_timestamp = pylsl.local_clock()
            for index, value in enumerate((1.0, 2.0, 3.0)):
                outlet.push_sample([value], start_timestamp + index * 0.1)
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                health = runtime.health(
                    WorkerCommand(
                        "health",
                        {"session_id": endpoint.session_id},
                        command_id=f"health-{time.monotonic_ns()}",
                    )
                )
                source_status = health["sources"]["synthetic"]
                if source_status["streams"][0]["sample_count"] >= 3 and health["backup"]["sample_count"] >= 1:
                    break
                time.sleep(0.05)
            frozen = runtime.freeze(reason="synthetic_test")
            self.assertEqual(frozen["quality_failures"], [])

            source_streams, _ = pyxdf.load_xdf(
                str(source_path), synchronize_clocks=False, handle_clock_resets=False,
                dejitter_timestamps=False, verbose=False,
            )
            backup_streams, _ = pyxdf.load_xdf(
                str(backup_path), synchronize_clocks=False, handle_clock_resets=False,
                dejitter_timestamps=False, verbose=False,
            )
            self.assertEqual(len(source_streams), 1)
            self.assertGreaterEqual(len(source_streams[0]["time_stamps"]), 3)
            self.assertEqual(len(backup_streams), 1)
            self.assertGreaterEqual(len(backup_streams[0]["time_stamps"]), 1)

            output = root / "derived" / "session.xdf"
            temporary = root / "derived" / ".session.xdf.xdf-merge-0123456789abcdef0123456789abcdef.tmp"
            source_artifacts = [
                {"path": str(source_path), "source_key": "synthetic", "sha256": sha256_file(source_path)},
                {"path": str(backup_path), "source_key": "derived_backup", "sha256": sha256_file(backup_path)},
            ]
            merged = runtime.merge_xdf(
                WorkerCommand(
                    "merge_xdf",
                    {
                        "session_id": endpoint.session_id,
                        "operation_id": "xdf-merge-0123456789abcdef0123456789abcdef",
                        "source_paths": [str(source_path), str(backup_path)],
                        "source_artifacts": source_artifacts,
                        "output_path": str(output),
                        "temporary_output_path": str(temporary),
                        "preserve_native_timestamps": True,
                        "preserve_clock_offsets": True,
                        "resample": False,
                        "atomic_publish": True,
                    },
                    command_id="merge-synthetic",
                )
            )
            self.assertEqual(merged["native_report"]["stream_count"], 2)
            merged_streams, _ = pyxdf.load_xdf(
                str(output), synchronize_clocks=False, handle_clock_resets=False,
                dejitter_timestamps=False, verbose=False,
            )
            self.assertEqual(len(merged_streams), 2)
            self.assertIn("study_runner_origin_id", json.dumps(merged_streams[0]["info"]))
        finally:
            if runtime is not None:
                runtime.abort_on_worker_failure()
                runtime.close_monitor()
            shutil.rmtree(root)


class _FakeWriter:
    def __init__(self) -> None:
        self.headers: list[tuple[int, str]] = []
        self.samples: list[tuple[int, list[float], list[tuple[float, ...]]]] = []
        self.footers: list[tuple[int, str]] = []
        self.closed = False

    def write_stream_header(self, stream_id, xml):
        self.headers.append((stream_id, xml))

    def write_samples(self, stream_id, timestamps, samples, **_kwargs):
        self.samples.append((stream_id, list(timestamps), [tuple(row) for row in samples]))

    def write_clock_offset(self, *_args):
        return None

    def write_stream_footer(self, stream_id, xml):
        self.footers.append((stream_id, xml))

    def boundary(self):
        return None

    def flush(self, **_kwargs):
        return None

    def close(self, **_kwargs):
        self.closed = True

    def abort(self, **_kwargs):
        self.closed = True

    def destroy(self):
        return None


class _FakeCore:
    def __init__(self) -> None:
        self.writer = _FakeWriter()

    def create_writer(self, _path):
        return self.writer


class _FakeInfo:
    def source_id(self):
        return "study_runner.fixture"

    def channel_count(self):
        return 1

    def type(self):
        return "TEST"

    def nominal_srate(self):
        return 10.0

    def channel_format(self):
        return 1

    def as_xml(self):
        return _stream_header("fixture", "study_runner.fixture", 10.0, "float32")


class _FakeInlet:
    def __init__(self, sample_seen: threading.Event) -> None:
        self.sample_seen = sample_seen
        self.sent = False

    def open_stream(self, **_kwargs):
        return None

    def pull_chunk(self, **_kwargs):
        if not self.sent:
            self.sent = True
            self.sample_seen.set()
            return [[1.5]], [50.0]
        time.sleep(0.005)
        return [], []

    def time_correction(self, **_kwargs):
        return 0.001

    def close_stream(self):
        return None


class _FakePylsl:
    cf_float32 = 1
    cf_double64 = 2
    cf_string = 3
    cf_int8 = 4
    cf_int16 = 5
    cf_int32 = 6
    cf_int64 = 7

    def __init__(self) -> None:
        self.sample_seen = threading.Event()
        self.info = _FakeInfo()

    def resolve_byprop(self, *_args, **_kwargs):
        return [self.info]

    def StreamInlet(self, *_args, **_kwargs):  # noqa: N802 - pylsl API
        return _FakeInlet(self.sample_seen)

    @staticmethod
    def local_clock():
        return 51.0


class LslSourceRecorderTests(unittest.TestCase):
    def test_projection_cache_degrades_last_real_value_then_becomes_stale_nan(self) -> None:
        stream = StreamSpec.from_manifest(
            {
                "key": "values",
                "source_id": "study_runner.fixture",
                "type": "TEST",
                "nominal_rate_hz": 10,
                "channel_format": "float32",
                "channels": ["value"],
                "channel_units": ["arbitrary"],
            }
        )
        cache = ProjectionCache()
        cache.update(
            "fixture",
            stream,
            [42.0],
            received_monotonic=0.0,
            source_timestamp=10.0,
            fallback_sequence=7,
        )
        self.assertTrue(cache.mark_degraded("fixture", "values"))
        cached = cache.get("fixture", "values")
        self.assertIsNotNone(cached)
        projection_payload = {
            "rate_hz": 1,
            "stale_after_ms": 2500,
            "channels": [{"output": "value", "stream": "values", "channel": "value"}],
        }
        sampler = BackupSampler(
            projections_from_manifest("fixture", projection_payload),
            start_monotonic=0.0,
        )
        sampler.update(
            "fixture",
            "values",
            {"value": 42.0},
            received_monotonic=cached.received_monotonic,
            source_timestamp=cached.source_timestamp,
            sequence=cached.sequence,
            source_ok=cached.source_ok,
        )

        degraded = sampler.emit_due(1.0)[0]
        stale = sampler.emit_due(3.0)[-1]

        self.assertEqual(degraded.values["fixture.values.value"], 42.0)
        self.assertEqual(degraded.values["fixture.values.valid"], 0.0)
        self.assertEqual(degraded.values["fixture.values.status"], STATUS_DEGRADED)
        self.assertTrue(math.isnan(stale.values["fixture.values.value"]))
        self.assertEqual(stale.values["fixture.values.status"], STATUS_STALE)

    def test_backup_header_embeds_status_and_projection_staleness_contract(self) -> None:
        core = _FakeCore()
        projection = {
            "plugin_key": "fixture",
            "rate_hz": 1,
            "stale_after_ms": 2500,
            "channels": [{"output": "value", "stream": "values", "channel": "value"}],
        }
        recorder = BackupRecorder(
            core,
            target_path=Path("backup.xdf"),
            payload={
                "projections": [projection],
                "grid_anchor_epoch": 100.0,
                "rate_hz": 1,
                "channel_names": [
                    "fixture.values.value",
                    "fixture.values.valid",
                    "fixture.values.sample_age_ms",
                    "fixture.values.sequence",
                    "fixture.values.status",
                ],
                "active_plugins": ["fixture"],
                "source_rates_hz": {"fixture.values": 10},
                "resampling_strategy": "latest_cached_at_slowest_projection_grid; stale_to_nan",
            },
            cache=ProjectionCache(),
            pylsl_module=_FakePylsl(),
            monotonic=lambda: 50.0,
            wall_clock=lambda: 100.0,
        )
        try:
            header = ElementTree.fromstring(core.writer.headers[0][1])
            status_codes = json.loads(header.findtext("./desc/status_codes") or "{}")
            projection_rules = json.loads(header.findtext("./desc/projection_rules") or "[]")
            self.assertEqual(status_codes["degraded"], STATUS_DEGRADED)
            self.assertEqual(projection_rules[0]["stale_after_seconds"], 2.5)
            self.assertIn("status=stale", header.findtext("./desc/staleness_rule") or "")
            self.assertIn("last_real_values", header.findtext("./desc/degraded_rule") or "")
        finally:
            recorder.abort()

    def test_worker_health_aggregates_source_and_backup_failures(self) -> None:
        class Probe:
            @staticmethod
            def as_dict():
                return {"canonical_xdf": True}

        class Recorder:
            @staticmethod
            def status():
                return {
                    "fatal_error": "checkpoint failed",
                    "streams": [
                        {
                            "key": "values",
                            "header_written": True,
                            "last_error": "LSL disconnected",
                        }
                    ],
                }

        class Backup:
            @staticmethod
            def status():
                return {"last_error": "durable flush failed"}

        runtime = RecordingWorkerRuntime.__new__(RecordingWorkerRuntime)
        runtime.session_id = "health-session"
        runtime.generation = 1
        runtime.core = type("Core", (), {"probe": Probe()})()
        runtime.lsl_versions = {"pylsl_package_version": "test"}
        runtime._lease_lock = threading.RLock()
        runtime._lease_until_epoch = time.time() + 60
        runtime._lock = threading.RLock()
        runtime._frozen = False
        runtime._freeze_reason = None
        runtime._sources = {"fixture": Recorder()}
        runtime._backup = Backup()
        runtime._merged_outputs = []

        result = runtime.health(
            WorkerCommand(
                "health",
                {"session_id": "health-session"},
                command_id="health-test",
            )
        )

        self.assertFalse(result["healthy"])
        self.assertEqual(result["status"], "attention_required")
        self.assertEqual(
            {issue["code"] for issue in result["issues"]},
            {"source_fatal_error", "lsl_stream_error", "backup_writer_error"},
        )

    def test_worker_health_marks_regular_primary_stream_stale(self) -> None:
        class Probe:
            @staticmethod
            def as_dict():
                return {"canonical_xdf": True}

        class Recorder:
            @staticmethod
            def status():
                return {
                    "fatal_error": None,
                    "streams": [
                        {
                            "key": "values",
                            "header_written": True,
                            "last_error": None,
                            "primary": True,
                            "nominal_rate_hz": 10.0,
                            "sample_count": 5,
                            "last_sample_age_seconds": 3.0,
                        },
                        {
                            "key": "markers",
                            "header_written": True,
                            "last_error": None,
                            "primary": False,
                            "nominal_rate_hz": 0.0,
                            "sample_count": 1,
                            "last_sample_age_seconds": 60.0,
                        },
                    ],
                }

        runtime = RecordingWorkerRuntime.__new__(RecordingWorkerRuntime)
        runtime.session_id = "stale-session"
        runtime.generation = 1
        runtime.core = type("Core", (), {"probe": Probe()})()
        runtime.lsl_versions = {}
        runtime._lease_lock = threading.RLock()
        runtime._lease_until_epoch = time.time() + 60
        runtime._lock = threading.RLock()
        runtime._frozen = False
        runtime._freeze_reason = None
        runtime._sources = {"fixture": Recorder()}
        runtime._backup = None
        runtime._merged_outputs = []

        result = runtime.health(
            WorkerCommand(
                "health",
                {"session_id": "stale-session"},
                command_id="health-stale",
            )
        )

        self.assertFalse(result["healthy"])
        self.assertEqual([issue["code"] for issue in result["issues"]], ["lsl_stream_stale"])

    def test_manifest_driven_source_records_and_closes_one_stream(self) -> None:
        core = _FakeCore()
        pylsl = _FakePylsl()
        recorder = LslSourceRecorder(
            core,
            plugin_key="fixture",
            target_path=Path("fixture.xdf"),
            streams=[
                {
                    "key": "values",
                    "source_id": "study_runner.fixture",
                    "type": "TEST",
                    "nominal_rate_hz": 10,
                    "channel_format": "float32",
                    "channels": ["value"],
                    "channel_units": ["arbitrary"],
                }
            ],
            cache=ProjectionCache(),
            pylsl_module=pylsl,
        )
        recorder.start()
        self.assertTrue(pylsl.sample_seen.wait(1.0))
        result = recorder.freeze(reason="test")

        self.assertTrue(result["closed"])
        self.assertEqual(len(core.writer.headers), 1)
        self.assertEqual(core.writer.samples[0][2], [(1.5,)])
        self.assertEqual(len(core.writer.footers), 1)
        self.assertTrue(core.writer.closed)

    def test_freeze_drains_sample_arriving_after_drain_request(self) -> None:
        class TailInlet(_FakeInlet):
            def __init__(self, sample_seen):
                super().__init__(sample_seen)
                self.tail_pull_started = threading.Event()
                self.release_tail = threading.Event()
                self.pull_number = 0

            def pull_chunk(self, **_kwargs):
                self.pull_number += 1
                if self.pull_number == 1:
                    self.sample_seen.set()
                    return [[1.0]], [50.0]
                if self.pull_number == 2:
                    self.tail_pull_started.set()
                    self.release_tail.wait(1.0)
                    return [[99.0]], [50.1]
                return [], []

        class TailPylsl(_FakePylsl):
            def __init__(self):
                super().__init__()
                self.inlet = TailInlet(self.sample_seen)

            def StreamInlet(self, *_args, **_kwargs):  # noqa: N802 - pylsl API
                return self.inlet

        core = _FakeCore()
        pylsl = TailPylsl()
        recorder = self._recorder(core, pylsl)
        recorder.start()
        self.assertTrue(pylsl.sample_seen.wait(1.0))
        self.assertTrue(pylsl.inlet.tail_pull_started.wait(1.0))
        outcome: dict[str, object] = {}

        def freeze() -> None:
            try:
                outcome["result"] = recorder.freeze(reason="tail_test")
            except Exception as error:  # pragma: no cover - asserted below
                outcome["error"] = error

        freezer = threading.Thread(target=freeze)
        freezer.start()
        self.assertTrue(recorder._drain_requested.wait(1.0))
        pylsl.inlet.release_tail.set()
        freezer.join(timeout=2.0)

        self.assertFalse(freezer.is_alive())
        self.assertNotIn("error", outcome)
        rows = [row for _stream, _timestamps, chunk in core.writer.samples for row in chunk]
        self.assertEqual(rows, [(1.0,), (99.0,)])
        self.assertTrue(core.writer.closed)

    def test_hung_inlet_causes_bounded_drain_failure_without_writer_use_after_free(self) -> None:
        class HungInlet(_FakeInlet):
            def __init__(self, sample_seen):
                super().__init__(sample_seen)
                self.pull_number = 0
                self.hung = threading.Event()
                self.release = threading.Event()

            def pull_chunk(self, **_kwargs):
                self.pull_number += 1
                if self.pull_number == 1:
                    self.sample_seen.set()
                    return [[1.0]], [50.0]
                self.hung.set()
                self.release.wait(5.0)
                return [], []

        class HungPylsl(_FakePylsl):
            def __init__(self):
                super().__init__()
                self.inlet = HungInlet(self.sample_seen)

            def StreamInlet(self, *_args, **_kwargs):  # noqa: N802 - pylsl API
                return self.inlet

        core = _FakeCore()
        pylsl = HungPylsl()
        recorder = self._recorder(core, pylsl)
        recorder.start()
        self.assertTrue(pylsl.inlet.hung.wait(1.0))
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "drain timed out"):
            recorder.freeze(reason="hung_test")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(core.writer.closed)
        pylsl.inlet.release.set()
        for thread in recorder._threads:
            thread.join(timeout=1.0)
        recorder.abort()
        self.assertTrue(core.writer.closed)

    def test_continuous_stream_uses_grace_cutover_and_closes_normally(self) -> None:
        class ContinuousInlet(_FakeInlet):
            def pull_chunk(self, **_kwargs):
                self.sample_seen.set()
                time.sleep(0.005)
                return [[1.0]], [time.monotonic()]

        class ContinuousPylsl(_FakePylsl):
            def __init__(self):
                super().__init__()
                self.inlet = ContinuousInlet(self.sample_seen)

            def StreamInlet(self, *_args, **_kwargs):  # noqa: N802 - pylsl API
                return self.inlet

        core = _FakeCore()
        pylsl = ContinuousPylsl()
        recorder = self._recorder(core, pylsl)
        recorder.start()
        self.assertTrue(pylsl.sample_seen.wait(1.0))

        started = time.monotonic()
        result = recorder.freeze(reason="continuous_test")

        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(result["closed"])
        self.assertTrue(core.writer.closed)
        self.assertGreaterEqual(result["streams"][0]["sample_count"], 1)

    def test_stream_metadata_mismatch_is_fail_closed(self) -> None:
        class WrongChannelInfo(_FakeInfo):
            def as_xml(self):
                return _stream_header("fixture", "study_runner.fixture", 10.0, "float32").replace(
                    "<label>value</label>", "<label>different</label>"
                )

        core = _FakeCore()
        recorder = LslSourceRecorder(
            core,
            plugin_key="fixture",
            target_path=Path("fixture.xdf"),
            streams=[
                {
                    "key": "values",
                    "source_id": "study_runner.fixture",
                    "type": "TEST",
                    "nominal_rate_hz": 10,
                    "channel_format": "float32",
                    "channels": ["value"],
                    "channel_units": ["arbitrary"],
                }
            ],
            cache=ProjectionCache(),
            pylsl_module=_FakePylsl(),
        )
        with self.assertRaisesRegex(RuntimeError, "labels/order"):
            recorder._validate_info(recorder._states[0].spec, WrongChannelInfo())
        recorder.abort()

    @staticmethod
    def _recorder(core, pylsl):
        return LslSourceRecorder(
            core,
            plugin_key="fixture",
            target_path=Path("fixture.xdf"),
            streams=[
                {
                    "key": "values",
                    "source_id": "study_runner.fixture",
                    "type": "TEST",
                    "nominal_rate_hz": 10,
                    "channel_format": "float32",
                    "channels": ["value"],
                    "channel_units": ["arbitrary"],
                }
            ],
            cache=ProjectionCache(),
            pylsl_module=pylsl,
        )


if __name__ == "__main__":
    unittest.main()
