from __future__ import annotations

from contextlib import ExitStack
import datetime as dt
import json
import math
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.recording.artifacts import SessionIdentity
from study_runner.backend.recording.coordinator import SegmentLedger
from study_runner.backend.recording.worker_binary import WorkerBinaryAvailability
from study_runner.backend.recording.worker_protocol import (
    LoopbackWorkerClient,
    WorkerEndpointState,
)
from study_runner.backend.services import (
    recording_dependencies,
    study_readiness_service,
    study_sensor_runtime,
    validation,
)
from study_runner.backend.services.card_summary_service import CardSummaryBuilder
from study_runner.backend.services.plugin_settings_service import (
    apply_plugin_settings,
    build_plugin_settings_schema,
)
from study_runner.backend.services.recording_runtime import RecordingRuntimeService
from study_runner.backend.services.recording_runtime_support import RECORDING_PLAN_SCHEMA
from study_runner.backend.services.study_plugin_config import normalize_card_plugin_actions
from study_runner.plugin_framework import registry
from study_runner.plugin_framework.plugin_catalog import PluginCatalog, discover_plugin_catalog


PLUGIN_KEY = "blueprint_sensor"
REFERENCE_SLOW_PLUGIN = "mini_radar"


class _MergedFixtureReader:
    """Stand in only for native XDF bytes; statistics stay in production code."""

    def read_streams(self, _path: Path):
        return [
            {
                "stream_key": f"{PLUGIN_KEY}.measurements",
                "plugin_key": PLUGIN_KEY,
                "source_id": f"study_runner.{PLUGIN_KEY}.measurements",
                "name": "Blueprint measurements",
                "nominal_rate_hz": 4,
                "channel_types": {"active": "boolean"},
                "timestamps": [100.0, 100.25, 100.5, 100.75, 101.0],
                "samples": [
                    {"value": 1.0, "active": True, "valid": True, "sequence": 1},
                    {"value": 2.0, "active": False, "valid": True, "sequence": 2},
                    {"value": 99.0, "active": True, "valid": False, "sequence": 4},
                    {"value": 4.0, "active": True, "valid": True, "sequence": 5},
                    {"value": 100.0, "active": True, "valid": True, "sequence": 6},
                ],
            }
        ]


class FixturePluginBlueprintAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = PROJECT_ROOT / ".tmp" / "fixture-plugin-blueprint"
        parent.mkdir(parents=True, exist_ok=True)
        self.root = parent / uuid.uuid4().hex
        self.root.mkdir()
        self.package_name = f"fixture_plugins_{self.root.name}"
        self.package_dir = self.root / self.package_name
        self.package_dir.mkdir()
        (self.package_dir / "__init__.py").write_text("", encoding="utf-8")
        sys.path.insert(0, str(self.root))

    def tearDown(self) -> None:
        root_text = str(self.root)
        if root_text in sys.path:
            sys.path.remove(root_text)
        for module_name in list(sys.modules):
            if module_name == self.package_name or module_name.startswith(
                f"{self.package_name}."
            ):
                sys.modules.pop(module_name, None)
        shutil.rmtree(self.root)

    def test_new_sensor_folder_drives_the_generic_recording_blueprint(self) -> None:
        self._write_fixture_plugin()
        fixture_catalog = discover_plugin_catalog(
            self.package_dir,
            package_name=self.package_name,
        )
        self.assertEqual([plugin.key for plugin in fixture_catalog.plugins], [PLUGIN_KEY])
        self.assertFalse(fixture_catalog.invalid_entries)

        original_catalog = registry.get_plugin_catalog()
        reference_entry = next(
            entry
            for entry in original_catalog.entries
            if entry.status == "valid" and entry.plugin_key == REFERENCE_SLOW_PLUGIN
        )
        # The shipped 1 Hz radar projection gives this fixture's 4 Hz projection
        # a genuine slower peer; no second synthetic plugin is needed.
        catalog = PluginCatalog(entries=(reference_entry, *fixture_catalog.entries))
        sensor_keys = (REFERENCE_SLOW_PLUGIN, PLUGIN_KEY)
        sensor_defaults = {REFERENCE_SLOW_PLUGIN: True, PLUGIN_KEY: False}

        with ExitStack() as patches:
            patches.enter_context(patch.object(registry, "_PLUGIN_CATALOG", catalog))
            patches.enter_context(patch.object(registry, "PLUGINS", catalog.plugins))
            patches.enter_context(
                patch.object(
                    registry,
                    "PLUGINS_BY_KEY",
                    {plugin.key: plugin for plugin in catalog.plugins},
                )
            )
            patches.enter_context(
                patch.object(study_sensor_runtime, "STUDY_SENSOR_KEYS", sensor_keys)
            )
            patches.enter_context(
                patch.object(study_sensor_runtime, "DEFAULT_STUDY_SENSORS", sensor_defaults)
            )
            patches.enter_context(
                patch.object(study_sensor_runtime, "SESSION_OVERRIDE_KEYS", sensor_keys)
            )
            patches.enter_context(
                patch.object(study_readiness_service, "STUDY_SENSOR_KEYS", sensor_keys)
            )
            patches.enter_context(patch.object(validation, "STUDY_SENSOR_KEYS", sensor_keys))

            public_plugin = registry.get_plugin_catalog_payload()["plugins_by_key"][PLUGIN_KEY]
            self.assertEqual(public_plugin["ui"]["label"], "Blueprint Sensor")
            self.assertEqual(
                public_plugin["capability_config"]["acquisition_transport"],
                {"transport": "serial", "delivery": "host_lsl_bridge"},
            )
            self.assertEqual(
                public_plugin["streams"][0]["source_id"],
                f"study_runner.{PLUGIN_KEY}.measurements",
            )
            self.assertEqual(public_plugin["streams"][0]["sequence_channel"], "sequence")
            self.assertIn("device_port", public_plugin["runtime_settings"])
            self.assertIn("calibration_scale", public_plugin["study_settings_schema"])
            self.assertIn("highlight_trace", public_plugin["card_actions_schema"])

            machine_config = {
                PLUGIN_KEY: {"enabled": True},
                REFERENCE_SLOW_PLUGIN: {"enabled": True},
            }
            settings_schema = build_plugin_settings_schema(machine_config)
            fixture_fields = {
                field["name"]: field for field in settings_schema[PLUGIN_KEY]["fields"]
            }
            self.assertEqual(fixture_fields["device_port"]["value"], "AUTO")
            machine_config, restart_required = apply_plugin_settings(
                machine_config,
                PLUGIN_KEY,
                {"device_port": "COM-42"},
            )
            self.assertTrue(restart_required)
            self.assertEqual(machine_config[PLUGIN_KEY]["transport"]["port"], "COM-42")

            study_settings = validation._validate_study_settings(
                {
                    "sensors_enabled": True,
                    "plugins": {
                        PLUGIN_KEY: {
                            "enabled": True,
                            "required": True,
                            "settings": {"calibration_scale": "2.5"},
                        },
                        REFERENCE_SLOW_PLUGIN: {
                            "enabled": True,
                            "required": True,
                            "settings": {},
                        },
                    },
                }
            )
            self.assertEqual(
                study_settings["plugins"][PLUGIN_KEY]["settings"]["calibration_scale"],
                2.5,
            )
            card_actions = normalize_card_plugin_actions(
                {"plugin_actions": {PLUGIN_KEY: {"highlight_trace": "false"}}}
            )
            self.assertFalse(card_actions[PLUGIN_KEY]["highlight_trace"])

            config = {"study_id": "blueprint-study", "study_settings": study_settings}
            selected = recording_dependencies.selected_recording_plugins(config)
            required = recording_dependencies.required_recording_plugins(config)
            self.assertEqual(set(selected), set(sensor_keys))
            self.assertEqual(set(required), set(sensor_keys))

            readiness = study_readiness_service.check_study_readiness(
                config,
                machine_config,
                {},
                https_active=False,
                recording_preflight={
                    "ready": True,
                    "selected_plugins": list(selected),
                    "required_plugins": list(required),
                },
            )
            self.assertTrue(readiness["ready"], readiness["blockers"])
            self.assertFalse(readiness["start_blocked"])

            context = registry.build_context(
                base_dir=self.root,
                data_dir=self.root / "data",
                hardware_config=machine_config,
                local_secrets={},
                local_secrets_file=self.root / "secrets.json",
            )
            status = registry.get_plugin_status(PLUGIN_KEY, context)
            self.assertEqual(status["status"], "ready")
            self.assertTrue(status["configured_enabled"])
            self.assertTrue(status["has_lsl"])
            self.assertTrue(status["has_recording"])

            paths, plan, commands = self._exercise_worker_plan(selected, required)
            source_commands = [
                command for command in commands if command["name"] == "start_recording_source"
            ]
            self.assertEqual(
                {command["payload"]["plugin_key"] for command in source_commands},
                set(sensor_keys),
            )
            fixture_source = next(
                command
                for command in source_commands
                if command["payload"]["plugin_key"] == PLUGIN_KEY
            )
            self.assertEqual(
                Path(fixture_source["payload"]["target_path"]).relative_to(paths.root).as_posix(),
                f"raw/plugins/{PLUGIN_KEY}/part-0001.xdf",
            )
            self.assertTrue(fixture_source["payload"]["require_stream_headers"])
            self.assertTrue(fixture_source["payload"]["require_fresh_primary_sample"])
            self.assertEqual(
                fixture_source["payload"]["streams"][0]["source_id"],
                f"study_runner.{PLUGIN_KEY}.measurements",
            )
            self.assertEqual(
                SegmentLedger(paths, PLUGIN_KEY).records()[0].state,
                "recording",
            )

            backup_command = next(
                command for command in commands if command["name"] == "start_backup_projection"
            )
            self.assertEqual(plan["backup"]["rate_hz"], 1.0)
            self.assertEqual(backup_command["payload"]["rate_hz"], 1.0)
            self.assertEqual(
                Path(backup_command["payload"]["target_path"])
                .relative_to(paths.root)
                .as_posix(),
                "raw/backup/slowest-grid_1hz.xdf",
            )
            backup_channels = plan["backup"]["channel_names"]
            self.assertIn(f"{PLUGIN_KEY}.measurements.value", backup_channels)
            self.assertIn(f"{PLUGIN_KEY}.measurements.valid", backup_channels)
            self.assertIn(f"{PLUGIN_KEY}.measurements.sample_age_ms", backup_channels)
            self.assertIn(f"{PLUGIN_KEY}.measurements.sequence", backup_channels)
            self.assertIn(f"{PLUGIN_KEY}.measurements.status", backup_channels)
            self.assertIn("mini_radar.vitals.heart_rate_bpm", backup_channels)
            self.assertEqual(plan["backup"]["artifact_role"], "derived_backup")

            merged = paths.merged_xdf
            merged.write_bytes(b"native-xdf-boundary-fixture")
            summary = CardSummaryBuilder(_MergedFixtureReader()).build(
                merged,
                [
                    {
                        "event_id": "fixture-card",
                        "question_index": 0,
                        "question_type": "stimulus",
                        "client_start_trigger_epoch_ms": 100_000,
                        "client_stop_trigger_epoch_ms": 101_000,
                    }
                ],
                session_id=paths.identity.session_id,
            )
            stream_summary = summary["cards"][0]["streams"][
                f"{PLUGIN_KEY}.measurements"
            ]
            self.assertEqual(stream_summary["count"], 4)
            self.assertEqual(stream_summary["valid_count"], 3)
            self.assertEqual(stream_summary["drop_count"], 1)
            numeric = stream_summary["channels"]["value"]
            self.assertAlmostEqual(numeric["mean"], 7 / 3)
            self.assertEqual(numeric["min"], 1.0)
            self.assertEqual(numeric["max"], 4.0)
            self.assertAlmostEqual(numeric["stddev"], math.sqrt(7 / 3))
            self.assertAlmostEqual(stream_summary["channels"]["active"]["mean"], 2 / 3)

        # Both the process-global catalog and imported fixture modules are
        # restored/removed by bounded test contexts, without module reloads.
        self.assertIs(registry.get_plugin_catalog(), original_catalog)
        self.assertIsNone(registry.get_plugin(PLUGIN_KEY))
        for relative_path in (
            "study_runner/backend/services/recording_runtime.py",
            "study_runner/web/scripts/lib/plugin-catalog.js",
            "study_runner/web/scripts/settings/study/study-settings-panel.js",
            "study_runner/web/scripts/cards/card-stimulus.js",
        ):
            self.assertNotIn(
                PLUGIN_KEY,
                (PROJECT_ROOT / relative_path).read_text(encoding="utf-8"),
                f"{relative_path} must remain plugin-key agnostic",
            )

    def _exercise_worker_plan(self, selected, required):
        runtime = RecordingRuntimeService(
            self.root / "saved_results",
            PROJECT_ROOT,
            clock=lambda: 1_000.5,
        )
        identity = SessionIdentity(
            study_id="blueprint-study",
            participant_id="participant-01",
            session_id="fixture-session",
            started_at=dt.datetime.fromtimestamp(1_000.0, tz=dt.timezone.utc),
        )
        paths = runtime.artifacts.reserve(identity)
        plan = {
            "schema": RECORDING_PLAN_SCHEMA,
            "study_id": identity.study_id,
            "participant_id": identity.participant_id,
            "session_id": identity.session_id,
            "started_at_epoch": 1_000.0,
            "status": "starting",
            "recording_plugins": list(selected),
            "required_source_keys": list(required),
            "backup": None,
            "worker": None,
            "last_error": None,
        }
        endpoint = WorkerEndpointState.create(
            session_id=identity.session_id,
            port=32_123,
            generation=1,
            clock=lambda: 1_000.0,
        )
        commands: list[dict] = []

        def transport(_endpoint, body, _headers, _timeout):
            command = json.loads(body.decode("utf-8"))
            commands.append(command)
            return {
                "protocol_version": 1,
                "command_id": command["command_id"],
                "ok": True,
                "result": {},
                "error": None,
                "replayed": False,
            }

        client = LoopbackWorkerClient(endpoint, transport=transport)
        availability = WorkerBinaryAvailability(
            available=True,
            path=None,
            protocol_version=1,
            kind="fixture-native-boundary",
            canonical_xdf=True,
            supports_merge=True,
        )
        runtime._start_worker_generation(
            paths,
            plan,
            availability,
            generation=1,
            endpoint=endpoint,
            client=client,
        )
        return paths, plan, commands

    def _write_fixture_plugin(self) -> None:
        plugin_dir = self.package_dir / PLUGIN_KEY
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
        manifest = {
            "api_version": 3,
            "plugin_key": PLUGIN_KEY,
            "version": "1.0.0",
            "category": "biosignal",
            "config_key": PLUGIN_KEY,
            "entry_point": "plugin:PLUGIN",
            "ui": {
                "label": "Blueprint Sensor",
                "description": "Temporary acceptance fixture for generic sensor integration.",
                "order": 9_999,
            },
            "capabilities": {
                "study_sensor": {"default_enabled": False, "default_required": True},
                "acquisition_transport": {
                    "transport": "serial",
                    "delivery": "host_lsl_bridge",
                },
                "lsl_stream_provider": {},
                "recording_source": {
                    "artifact": "xdf",
                    "primary_stream": "measurements",
                },
                "backup_projection": {
                    "rate_hz": 4,
                    "stale_after_ms": 750,
                    "channels": [
                        {
                            "output": "value",
                            "stream": "measurements",
                            "channel": "value",
                        }
                    ],
                },
                "readiness": {},
                "health": {},
                "machine_settings": {},
                "study_settings": {},
                "card_actions": {},
            },
            "streams": [
                {
                    "key": "measurements",
                    "source_id": f"study_runner.{PLUGIN_KEY}.measurements",
                    "type": "BLUEPRINT",
                    "nominal_rate_hz": 4,
                    "clock_domain": "lsl",
                    "channel_format": "float32",
                    "channels": ["value", "active", "sequence"],
                    "channel_units": ["arbitrary_unit", "boolean", "count"],
                    "sequence_channel": "sequence",
                }
            ],
            "settings": {
                "machine": {
                    "device_port": {
                        "type": "string",
                        "path": "transport.port",
                        "default": "AUTO",
                        "label_key": "pluginSettings.blueprint.devicePort",
                        "apply": "restart",
                        "scope": "machine",
                    }
                },
                "study": {
                    "calibration_scale": {
                        "type": "number",
                        "default": 1.0,
                        "minimum": 0.1,
                        "maximum": 10.0,
                    }
                },
                "card_actions": {
                    "highlight_trace": {"type": "boolean", "default": True}
                },
            },
            "poll_interval_ms": 500,
            "request_timeout_ms": 250,
            "clock_domain": "lsl",
            "expected_data_rate": {"measurements_hz": 4},
            "backpressure": {"max_in_flight": 1, "drop_policy": "latest_status_wins"},
        }
        (plugin_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        (plugin_dir / "plugin.py").write_text(
            "from study_runner.plugin_framework.plugin_api import Plugin\n\n"
            "def _status(context):\n"
            f"    configured = bool(context.hardware_config.get({PLUGIN_KEY!r}, {{}}).get('enabled'))\n"
            "    return {\n"
            "        'status': 'ready' if configured else 'disabled',\n"
            "        'configured_enabled': configured,\n"
            "        'lsl_enabled': configured,\n"
            "        'last_message': 'fixture ready' if configured else 'fixture disabled',\n"
            "    }\n\n"
            "PLUGIN = Plugin(\n"
            f"    key={PLUGIN_KEY!r},\n"
            "    label='Blueprint Sensor',\n"
            "    category='biosignal',\n"
            f"    config_key={PLUGIN_KEY!r},\n"
            "    has_lsl=True,\n"
            "    has_recording=True,\n"
            "    get_status=_status,\n"
            ")\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
