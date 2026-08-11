from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# An absent worker is unavailable for a different reason on each platform: off Linux
# the bundled binary is simply missing, while Linux stays fail-closed by policy until
# a canonical XDF core passes its release gate. Either way the state must be explicit.
WORKER_UNAVAILABLE_REASON = (
    "fail-closed" if platform.system().strip().casefold() == "linux" else "not found"
)

from study_runner.recording.artifacts import ArtifactStore, SessionIdentity
from study_runner.recording.coordinator import SegmentLedger
from study_runner.recording.recovery import RecordingLeaseStore
from study_runner.recording.worker_binary import WorkerBinaryAvailability
from study_runner.recording.worker_protocol import LoopbackWorkerClient, WorkerEndpointState
from study_runner.recording.xdf import StreamInspection, XdfArtifactInspection
from study_runner.recording.errors import WorkerUnavailableError
from study_runner.backend.services.recording.recording_runtime import (
    NativeWorkerLauncher,
    RecordingRuntimeService,
    RuntimeRecordingFinalizationAdapter,
    WorkerLaunchSpec,
    _backup_source_checks,
    _recording_lease_quality_checks,
    _recovery_backup_grid_anchor,
    recording_lsl_dependency_status,
)
from study_runner.backend.services.recording.recording_contract import (
    build_recording_contract,
    load_recording_contract,
)
from study_runner.backend.services.recording.recording_runtime_support import RecordingRuntimeError
from study_runner.plugin_framework.registry import get_plugin_manifest
from study_runner.backend.services.recording.recording_quality import scientific_source_checks
from study_runner.backend.services.delivery.finalization_service import FinalizationError
from study_runner.backend.services.recording.recording_dependencies import (
    PINNED_PYLSL_VERSION,
    probe_lsl_dependencies,
)


class FakeLauncher:
    commands: list[dict] = []

    def __init__(self, _binary: Path) -> None:
        pass

    def launch(self, paths, *, generation=1):
        endpoint = WorkerEndpointState.create(
            session_id=paths.identity.session_id,
            port=32123,
            generation=generation,
        )
        paths.worker_state_file.write_text(json.dumps(endpoint.as_dict()), encoding="utf-8")

        def transport(_endpoint, body, _headers, _timeout):
            command = json.loads(body.decode("utf-8"))
            self.commands.append(command)
            return {
                "protocol_version": 1,
                "command_id": command["command_id"],
                "ok": True,
                "result": {},
                "error": None,
                "replayed": False,
            }

        return endpoint, LoopbackWorkerClient(endpoint, transport=transport)


class DeadFirstGenerationRuntime(RecordingRuntimeService):
    def _healthy_client(self, paths, endpoint):
        if endpoint.generation == 1:
            raise WorkerUnavailableError("simulated worker crash")
        return super()._healthy_client(paths, endpoint)


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value


class RecordingRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeLauncher.commands = []

    def test_session_starts_one_xdf_per_plugin_marker_and_slowest_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binary = root / "fake-worker.exe"
            binary.write_bytes(b"worker")
            runtime = RecordingRuntimeService(
                root / "saved_results",
                root,
                configured_worker_path=binary,
                launcher_factory=FakeLauncher,
            )
            config = {
                "study_id": "study",
                "study_settings": {
                    "plugins": {
                        "brainbit": {"enabled": True, "required": True, "settings": {}},
                        "mini_radar": {"enabled": True, "required": True, "settings": {}},
                    }
                },
            }
            result = runtime.start_session(
                {
                    "study_id": "study",
                    "participant_id": "p/01",
                    "session_id": "session-1",
                    "started_at_epoch": 1_753_920_000.0,
                },
                config,
                {"lsl": {"enabled": True}},
            )

            self.assertEqual(result["status"], "recording")
            self.assertEqual(
                result["plugins"],
                ["brainbit", "mini_radar", "lsl", "clock_diagnostics"],
            )
            self.assertEqual(result["backup"]["rate_hz"], 1.0)
            self.assertTrue(result["backup"]["relative_path"].endswith("slowest-grid_1hz.xdf"))
            names = [command["name"] for command in FakeLauncher.commands]
            self.assertEqual(names.count("start_recording_source"), 4)
            self.assertEqual(names.count("start_backup_projection"), 1)
            backup_command = next(
                command
                for command in FakeLauncher.commands
                if command["name"] == "start_backup_projection"
            )
            channel_names = result["backup"]["channel_names"]
            self.assertEqual(backup_command["payload"]["channel_names"], channel_names)
            self.assertIn("brainbit.mental.attention", channel_names)
            self.assertIn("brainbit.mental.valid", channel_names)
            self.assertIn("mini_radar.vitals.heart_rate_bpm", channel_names)
            self.assertIn("mini_radar.vitals.status", channel_names)
            self.assertNotIn("valid", channel_names)

            session_roots = list((root / "saved_results" / "study" / "participants").glob("*/sessions/*"))
            self.assertEqual(len(session_roots), 1)
            self.assertTrue((session_roots[0] / "raw/plugins/brainbit/segments.json").is_file())
            plan = json.loads(
                (session_roots[0] / "recording-plan.json").read_text(encoding="utf-8")
            )
            contract = load_recording_contract(plan)
            self.assertIsNotNone(contract)
            self.assertEqual(contract["schema"], "study-runner/recording-contract/v1")
            self.assertEqual(contract["selected_source_keys"], result["plugins"])
            self.assertEqual(
                contract["streams_by_source"]["brainbit"],
                contract["source_manifests"]["brainbit"]["streams"],
            )
            self.assertEqual(contract["backup"]["channel_names"], channel_names)
            self.assertEqual(len(contract["sha256"]), 64)

    def test_device_confirmed_channels_are_sealed_separately_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binary = root / "fake-worker.exe"
            binary.write_bytes(b"worker")
            runtime = RecordingRuntimeService(
                root / "saved_results",
                root,
                configured_worker_path=binary,
                launcher_factory=FakeLauncher,
            )
            manifest_eeg = next(
                stream
                for stream in get_plugin_manifest("brainbit")["streams"]
                if stream["key"] == "eeg"
            )
            actual_eeg = {
                **manifest_eeg,
                "channels": ["O1", "O2", "T3", "T4", "F3", "F4", "C3", "C4"],
                "channel_units": ["microvolt"] * 8,
            }
            runtime.start_session(
                {
                    "study_id": "study",
                    "participant_id": "p01",
                    "session_id": "dynamic-contract",
                    "started_at_epoch": 1_753_920_000.0,
                },
                {
                    "study_id": "study",
                    "study_settings": {
                        "plugins": {
                            "brainbit": {"enabled": True, "required": True, "settings": {}}
                        }
                    },
                },
                {"brainbit": {"enabled": True}},
                {
                    "brainbit": {
                        "ok": True,
                        "result": {"actual_streams": [actual_eeg]},
                    }
                },
            )

            plan_path = next((root / "saved_results").glob("*/participants/*/sessions/*/recording-plan.json"))
            contract = load_recording_contract(json.loads(plan_path.read_text(encoding="utf-8")))
            self.assertIsNotNone(contract)

        self.assertEqual(len(contract["source_manifests"]["brainbit"]["streams"][0]["channels"]), 4)
        self.assertEqual(contract["streams_by_source"]["brainbit"][0]["channels"], actual_eeg["channels"])
        self.assertEqual(
            contract["source_descriptors"]["brainbit"]["stream_contract_origin"],
            "runtime",
        )
        self.assertEqual(len(contract["source_descriptors"]["brainbit"]["manifest_sha256"]), 64)

    def test_dynamic_stream_plugin_cannot_fall_back_to_manifest_in_http_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binary = root / "fake-worker.exe"
            binary.write_bytes(b"worker")
            runtime = RecordingRuntimeService(
                root / "saved_results",
                root,
                configured_worker_path=binary,
                launcher_factory=FakeLauncher,
            )
            with self.assertRaisesRegex(RecordingRuntimeError, "runtime stream contract"):
                runtime.start_session(
                    {
                        "study_id": "study",
                        "participant_id": "p01",
                        "session_id": "missing-contract",
                        "started_at_epoch": 1_753_920_000.0,
                    },
                    {
                        "study_id": "study",
                        "study_settings": {
                            "plugins": {
                                "brainbit": {"enabled": True, "required": True, "settings": {}}
                            }
                        },
                    },
                    {"brainbit": {"enabled": True}},
                    {},
                )

    def test_missing_worker_is_fail_closed_in_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = RecordingRuntimeService(root / "saved_results", root)
            report = runtime.preflight(
                {
                    "study_settings": {
                        "plugins": {
                            "brainbit": {"enabled": True, "required": True, "settings": {}},
                        }
                    }
                }
            )

        self.assertFalse(report["ready"])
        self.assertFalse(report["available"])
        self.assertEqual(report["required_plugins"], ["brainbit"])
        self.assertIn(WORKER_UNAVAILABLE_REASON, report["reason"])

    def test_reused_session_replaces_dead_worker_and_opens_part_0002(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binary = root / "fake-worker.exe"
            binary.write_bytes(b"worker")
            clock = MutableClock(100.0)
            runtime = DeadFirstGenerationRuntime(
                root / "saved_results",
                root,
                configured_worker_path=binary,
                launcher_factory=FakeLauncher,
                clock=clock,
            )
            config = {
                "study_id": "study",
                "study_settings": {
                    "plugins": {
                        "brainbit": {"enabled": True, "required": True, "settings": {}},
                    }
                },
            }
            session = {
                "study_id": "study",
                "participant_id": "p01",
                "session_id": "session-recovery",
                "started_at_epoch": 1_753_920_000.0,
            }
            runtime.start_session(session, config, {"lsl": {"enabled": True}})
            clock.value = 112.4
            recovered = runtime.start_session(
                {**session, "reused": True},
                config,
                {"lsl": {"enabled": True}},
            )

            self.assertEqual(recovered["worker"]["generation"], 2)
            session_root = next((root / "saved_results").glob("*/participants/*/sessions/*"))
            ledger = json.loads(
                (session_root / "raw/plugins/brainbit/segments.json").read_text(encoding="utf-8")
            )
            self.assertEqual([item["number"] for item in ledger["segments"]], [1, 2])
            self.assertEqual(ledger["segments"][0]["state"], "interrupted")
            self.assertEqual(ledger["segments"][1]["state"], "recording")
            plan = json.loads((session_root / "recording-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(len(plan["backup"]["segments"]), 2)
            self.assertIn("recovery-0002.xdf", plan["backup"]["segments"][1]["relative_path"])
            self.assertEqual(plan["backup"]["segments"][1]["grid_anchor_epoch"], 112.0)

    def test_recovery_uses_snapshot_after_all_catalog_plugins_disappear(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binary = root / "fake-worker.exe"
            binary.write_bytes(b"worker")
            runtime = DeadFirstGenerationRuntime(
                root / "saved_results",
                root,
                configured_worker_path=binary,
                launcher_factory=FakeLauncher,
            )
            config = {
                "study_id": "study",
                "study_settings": {
                    "plugins": {
                        "brainbit": {"enabled": True, "required": True, "settings": {}},
                    }
                },
            }
            session = {
                "study_id": "study",
                "participant_id": "p01",
                "session_id": "session-plugin-removal",
                "started_at_epoch": 1_753_920_000.0,
            }
            runtime.start_session(session, config, {"lsl": {"enabled": True}})

            with (
                mock.patch(
                    "study_runner.backend.services.recording.recording_dependencies.get_plugin_manifests",
                    return_value={},
                ),
                mock.patch(
                    "study_runner.backend.services.recording.recording_runtime.get_plugin_manifests_with_internal_sources",
                    side_effect=AssertionError("recovery consulted the live catalog"),
                ),
                mock.patch(
                    "study_runner.backend.services.recording.recording_runtime.get_backup_projection_specs",
                    side_effect=AssertionError("recovery rebuilt backup from the live catalog"),
                ),
            ):
                recovered = runtime.start_session(
                    {**session, "reused": True},
                    config,
                    {"lsl": {"enabled": True}},
                )

            self.assertEqual(recovered["worker"]["generation"], 2)
            brainbit_starts = [
                command
                for command in FakeLauncher.commands
                if command["name"] == "start_recording_source"
                and command["payload"]["plugin_key"] == "brainbit"
            ]
            self.assertEqual(len(brainbit_starts), 2)
            self.assertGreater(len(brainbit_starts[1]["payload"]["streams"]), 0)
            backup_starts = [
                command for command in FakeLauncher.commands
                if command["name"] == "start_backup_projection"
            ]
            self.assertEqual(
                backup_starts[1]["payload"]["projections"],
                backup_starts[0]["payload"]["projections"],
            )

    def test_scientific_checks_fail_closed_on_tamper_and_ignore_live_catalog(self) -> None:
        manifest = {
            "plugin_key": "fixture",
            "version": "1.0.0",
            "capabilities": ["study_sensor", "recording_source"],
            "streams": [
                {
                    "key": "signal",
                    "source_id": "fixture.original",
                    "nominal_rate_hz": 10,
                    "channels": ["value"],
                    "channel_units": ["arbitrary_unit"],
                }
            ],
        }
        backup = {
            "rate_hz": 1.0,
            "artifact_role": "derived_backup",
            "resampling_strategy": "latest_cached_at_slowest_projection_grid; stale_to_nan",
            "quality_channels": ["valid", "sample_age_ms", "sequence", "status"],
            "channel_names": ["fixture.signal.value"],
            "source_rates_hz": {"fixture.signal": 10},
            "active_plugins": ["fixture"],
            "projections": [
                {
                    "plugin_key": "fixture",
                    "rate_hz": 1.0,
                    "channels": [],
                }
            ],
        }
        contract = build_recording_contract(
            ["fixture"], ["fixture"], {"fixture": manifest}, backup
        )
        plan = {
            "recording_plugins": ["fixture"],
            "required_source_keys": ["fixture"],
            "recording_contract": contract,
            "backup": None,
        }
        with mock.patch(
            "study_runner.backend.services.recording.recording_quality.get_plugin_manifests_with_internal_sources",
            side_effect=AssertionError("quality checks consulted the live catalog"),
        ):
            issues, metrics = scientific_source_checks(plan, [])
        self.assertIn("missing_declared_stream", {issue.code for issue in issues})
        self.assertTrue(metrics["recording_contract"]["valid"])

        plan["recording_contract"]["streams_by_source"]["fixture"][0]["source_id"] = (
            "fixture.tampered"
        )
        issues, metrics = scientific_source_checks(plan, [])
        self.assertIn("recording_contract_invalid", {issue.code for issue in issues})
        self.assertFalse(metrics["recording_contract"]["valid"])

    def test_hybrid_launcher_uses_python_entrypoint_and_explicit_core(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            software_root = root / "software"
            software_root.mkdir()
            (software_root / "server.py").write_text("# entrypoint\n", encoding="utf-8")
            core = root / "xdf_core.dll"
            core.write_bytes(b"core")
            identity = SessionIdentity(
                study_id="study",
                participant_id="p01",
                session_id="launcher-session",
                started_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            )
            paths = ArtifactStore(root / "saved_results").reserve(identity)
            availability = WorkerBinaryAvailability(
                available=True,
                path=core,
                core_path=core,
                protocol_version=1,
                kind="hybrid_core",
                canonical_xdf=True,
                supports_merge=True,
            )
            captured: dict = {}

            class Process:
                pid = 4321
                returncode = None

                @staticmethod
                def poll():
                    return None

            def popen(command, **kwargs):
                captured["command"] = command
                captured["kwargs"] = kwargs
                return Process()

            class Client:
                def __init__(self, *_args, **_kwargs):
                    pass

                @staticmethod
                def send(*_args, **_kwargs):
                    return type("Response", (), {"ok": True, "error": None})()

            launcher = NativeWorkerLauncher(
                WorkerLaunchSpec(availability, software_root),
                popen=popen,
            )
            with mock.patch(
                "study_runner.backend.services.recording.recording_runtime.LoopbackWorkerClient",
                Client,
            ):
                endpoint, _client = launcher.launch(paths)

            self.assertEqual(endpoint.pid, 4321)
            self.assertEqual(
                captured["command"][:3],
                [sys.executable, str((software_root / "server.py").resolve()), "--recording-worker"],
            )
            self.assertEqual(
                captured["command"][captured["command"].index("--xdf-core") + 1],
                str(core),
            )
            self.assertIs(captured["kwargs"]["stdin"], __import__("subprocess").DEVNULL)
            if sys.platform == "win32":
                import subprocess

                expected = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                self.assertEqual(captured["kwargs"]["creationflags"] & expected, expected)
                self.assertFalse(captured["kwargs"]["start_new_session"])
            else:
                self.assertTrue(captured["kwargs"]["start_new_session"])

    def test_expired_recording_lease_is_mandatory_quality_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identity = SessionIdentity(
                study_id="study",
                participant_id="p01",
                session_id="expired-session",
                started_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            )
            paths = ArtifactStore(root).reserve(identity)
            clock = MutableClock(100.0)
            store = RecordingLeaseStore(
                paths.recording_lease_file,
                clock=clock,
                lease_seconds=10.0,
            )
            store.start(identity.session_id, worker_generation=1)
            clock.value = 111.0
            store.expire_if_due()

            issues, metrics = _recording_lease_quality_checks(paths, {})

            self.assertIn("web_server_lease_expired", {issue.code for issue in issues})
            self.assertEqual(metrics["recording_lease"]["state"], "expired")

    def test_producer_stop_failure_freezes_worker_then_fails_finalization_step(self) -> None:
        class Runtime:
            called = False

            def freeze_worker(self, _paths, *, command_id):
                self.called = True
                return {"command_id": command_id}

        runtime = Runtime()
        adapter = RuntimeRecordingFinalizationAdapter(
            runtime,
            stop_producers=lambda _context: {
                "runtime": {"brainbit": {"ok": False, "error": "stop timeout"}}
            },
        )
        context = type(
            "Context",
            (),
            {
                "recording_expected": True,
                "paths": object(),
                "state": {
                    "job_id": "job-1",
                    "steps": [{"key": "freeze_recording", "attempts": 1}],
                },
            },
        )()

        with self.assertRaisesRegex(FinalizationError, "brainbit: stop timeout"):
            adapter.freeze(context)
        self.assertTrue(runtime.called)

    def test_worker_freeze_quality_failure_fails_finalization_step(self) -> None:
        class Runtime:
            def freeze_worker(self, _paths, *, command_id):
                return {
                    "command_id": command_id,
                    "quality_failures": ["derived_backup: durable flush failed"],
                }

        adapter = RuntimeRecordingFinalizationAdapter(Runtime())
        context = type(
            "Context",
            (),
            {
                "recording_expected": True,
                "paths": object(),
                "state": {
                    "job_id": "job-1",
                    "steps": [{"key": "freeze_recording", "attempts": 1}],
                },
            },
        )()

        with self.assertRaisesRegex(FinalizationError, "durable flush failed"):
            adapter.freeze(context)

    def test_recovery_backup_anchor_skips_historical_deadlines(self) -> None:
        self.assertEqual(_recovery_backup_grid_anchor(100.0, 2.0, 112.4), 112.0)

    def test_lsl_dependency_probe_fails_closed_before_session_start(self) -> None:
        with mock.patch(
            "study_runner.backend.services.recording.recording_runtime.require_pylsl",
            side_effect=RuntimeError("liblsl missing"),
        ):
            status = recording_lsl_dependency_status()

        self.assertFalse(status["ok"])
        self.assertIn("liblsl missing", status["reason"])

    def test_lsl_dependency_probe_requires_exact_pylsl_version(self) -> None:
        status = probe_lsl_dependencies(
            lambda: object(),
            lambda _module: {
                "pylsl_package_version": "1.18.1",
                "liblsl_library_version": 117,
                "version_probe_error": None,
            },
        )

        self.assertFalse(status["ok"])
        self.assertIn("pylsl=1.18.1", status["reason"])
        self.assertIn(PINNED_PYLSL_VERSION, status["reason"])
        self.assertEqual(status["liblsl_library_version"], 117)

    def test_lsl_dependency_probe_rejects_missing_pylsl_package_version(self) -> None:
        status = probe_lsl_dependencies(
            lambda: object(),
            lambda _module: {
                "pylsl_package_version": "",
                "liblsl_library_version": 117,
                "version_probe_error": None,
            },
        )

        self.assertFalse(status["ok"])
        self.assertIn("pylsl=missing", status["reason"])
        self.assertEqual(status["liblsl_library_version"], 117)

    def test_lsl_dependency_probe_accepts_exact_pin_and_keeps_native_provenance(self) -> None:
        status = probe_lsl_dependencies(
            lambda: object(),
            lambda _module: {
                "pylsl_package_version": PINNED_PYLSL_VERSION,
                "liblsl_library_version": 117,
                "version_probe_error": None,
            },
        )

        self.assertTrue(status["ok"])
        self.assertIsNone(status["reason"])
        self.assertEqual(status["pylsl_package_version"], PINNED_PYLSL_VERSION)
        self.assertEqual(status["liblsl_library_version"], 117)

    def test_declared_missing_middle_segment_is_not_filtered_from_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            identity = SessionIdentity(
                study_id="study",
                participant_id="p01",
                session_id="segments-session",
                started_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            )
            runtime = RecordingRuntimeService(root, PROJECT_ROOT)
            paths = runtime.artifacts.reserve(identity)
            (paths.root / "recording-plan.json").write_text(
                json.dumps(
                    {
                        "schema": "study-runner/recording-plan/v1",
                        "recording_plugins": ["fixture"],
                        "required_source_keys": ["fixture"],
                        "backup": None,
                    }
                ),
                encoding="utf-8",
            )
            ledger = SegmentLedger(paths, "fixture")
            records = [
                ledger.allocate(f"allocation-{index}", worker_generation=index)
                for index in range(1, 4)
            ]
            ledger.absolute_path(records[0]).write_bytes(b"part-1")
            ledger.absolute_path(records[2]).write_bytes(b"part-3")

            class Inspector:
                def inspect(self, path, *, source_key, **_kwargs):
                    return XdfArtifactInspection(
                        path=path,
                        source_key=source_key,
                        readable=path.is_file(),
                        file_sha256="hash" if path.is_file() else None,
                        streams=(),
                        error=None if path.is_file() else "file does not exist",
                    )

            with mock.patch(
                "study_runner.backend.services.recording.recording_runtime.PyXdfInspector",
                return_value=Inspector(),
            ):
                inspections, report = runtime.inspect_sources(paths)

            self.assertEqual(len(inspections), 3)
            self.assertFalse(inspections[1].readable)
            self.assertIn("unreadable_source", {issue.code for issue in report.issues})

    def test_backup_validator_requires_sampler_qualified_channel_names(self) -> None:
        strategy = "latest_cached_at_slowest_projection_grid; stale_to_nan"
        expected_labels = (
            "sensor.vitals.value",
            "sensor.vitals.valid",
            "sensor.vitals.sample_age_ms",
            "sensor.vitals.sequence",
            "sensor.vitals.status",
        )
        plan = {
            "backup": {
                "rate_hz": 1.0,
                "artifact_role": "derived_backup",
                "resampling_strategy": strategy,
                "active_plugins": [],
                "channel_names": list(expected_labels),
                "projections": [
                    {
                        "plugin_key": "sensor",
                        "rate_hz": 1.0,
                        "stale_after_ms": 2500,
                        "channels": [
                            {"output": "value", "stream": "vitals", "channel": "source_value"}
                        ],
                    }
                ],
            }
        }
        stream = StreamInspection(
            origin_id="derived_backup:slowest-grid_1hz.xdf:0",
            name="StudyRunnerBackup",
            stream_type="DerivedBackup",
            source_id="study_runner.derived_backup",
            nominal_srate=1.0,
            channel_count=len(expected_labels),
            sample_count=1,
            first_timestamp=1.0,
            last_timestamp=1.0,
            sample_hash="samples",
            timestamp_hash="timestamps",
            clock_offsets_hash="offsets",
            metadata_hash="metadata",
            stream_id="backup-1",
            channel_labels=expected_labels,
            artifact_role="derived_backup",
            resampling_strategy=strategy,
        )
        artifact = XdfArtifactInspection(
            path=Path("slowest-grid_1hz.xdf"),
            source_key="derived_backup",
            readable=True,
            file_sha256="hash",
            streams=(stream,),
        )
        issues = []

        metrics = _backup_source_checks(plan, [artifact], issues)

        self.assertEqual(issues, [])
        self.assertEqual(metrics["expected_channel_names"], sorted(expected_labels))
        self.assertEqual(metrics["missing_projection_channels"], [])
        self.assertEqual(metrics["missing_quality_channels"], [])

        bare_stream = StreamInspection(
            **{
                **stream.__dict__,
                "channel_labels": ("value", "valid", "sample_age_ms", "sequence", "status"),
            }
        )
        bare_issues = []
        _backup_source_checks(
            plan,
            [
                XdfArtifactInspection(
                    path=artifact.path,
                    source_key=artifact.source_key,
                    readable=True,
                    file_sha256=artifact.file_sha256,
                    streams=(bare_stream,),
                )
            ],
            bare_issues,
        )
        self.assertIn(
            "backup_projection_channels_missing",
            {issue.code for issue in bare_issues},
        )

    def test_dead_frozen_worker_is_replaced_for_merge_without_new_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binary = root / "fake-worker.exe"
            binary.write_bytes(b"worker")
            runtime = DeadFirstGenerationRuntime(
                root / "saved_results",
                root,
                configured_worker_path=binary,
                launcher_factory=FakeLauncher,
            )
            config = {
                "study_id": "study",
                "study_settings": {
                    "plugins": {
                        "brainbit": {"enabled": True, "required": True, "settings": {}},
                    }
                },
            }
            runtime.start_session(
                {
                    "study_id": "study",
                    "participant_id": "p01",
                    "session_id": "session-merge-recovery",
                    "started_at_epoch": 1_753_920_000.0,
                },
                config,
                {"lsl": {"enabled": True}},
            )
            session_root = next((root / "saved_results").glob("*/participants/*/sessions/*"))
            plan_path = session_root / "recording-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["status"] = "frozen"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            paths = runtime._find_paths("session-merge-recovery")
            self.assertIsNotNone(paths)
            source_starts_before = sum(
                command["name"] == "start_recording_source" for command in FakeLauncher.commands
            )

            backend = runtime._backend_for_merge(paths)

            self.assertEqual(backend.coordinator.worker.endpoint.generation, 2)
            source_starts_after = sum(
                command["name"] == "start_recording_source" for command in FakeLauncher.commands
            )
            self.assertEqual(source_starts_after, source_starts_before)
            recovered_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered_plan["merge_worker_generation"], 2)


if __name__ == "__main__":
    unittest.main()
