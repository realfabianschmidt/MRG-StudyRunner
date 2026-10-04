"""Stimulus actuators, the end of a stimulus card's time, and its overtime.

Rules held for every plugin:

- A plugin is an actuator exactly when its manifest declares trial ``start``
  and ``stop``; it then implements both hooks. Sensors declare neither: they
  record continuously and a card only marks its phases with markers.
- Start and stop reach only the actuators the card selected. The core reads
  that from the manifests and never names a plugin.
- A card that lets the participant stay past its duration ("overtime") keeps
  its own window comparable (start to time-up) and reports the overtime as a
  window of its own; the server's safety stop fires the event that really
  stops the actuators.
"""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.card_validation_primitives import CardValidationError
from study_runner.contracts.plugin_api import Plugin
from study_runner.plugin_framework import registry
from study_runner.plugin_framework.registry import (
    get_plugin_manifests,
    is_stimulus_actuator,
    run_trial_marker,
    run_trial_start,
    run_trial_stop,
    stimulus_actuator_keys,
)
from study_runner.plugins.cards.stimulus import plugin as stimulus_card
from study_runner.runtime_core.studies import results_service
from study_runner.runtime_core.studies import study_plugin_config
from study_runner.runtime_core.studies.card_summary_service import CardSummaryBuilder
from study_runner.runtime_core.studies.study_plugin_config import (
    PluginConfigError,
    normalize_card_actuator_plugins,
)
from study_runner.runtime_core.studies.trial_event_service import (
    TrialEventService,
    stop_deadline_for,
)


SOFTWARE = PROJECT_ROOT
PLUGINS_DIR = SOFTWARE / "study_runner" / "plugins"


def _actuator_manifest() -> dict:
    return {"runtime": {"trial_events": ["start", "stop"]}, "capabilities": []}


def _sensor_manifest() -> dict:
    return {"runtime": {"trial_events": ["session_end"]}, "capabilities": ["recording_source"]}


class ActuatorsAreDeclaredInTheManifestTests(unittest.TestCase):
    def test_start_and_stop_are_declared_together_and_both_hooks_exist(self) -> None:
        for manifest_path in sorted(PLUGINS_DIR.glob("*/*/manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            events = set((manifest.get("runtime") or {}).get("trial_events") or [])
            with self.subTest(plugin=manifest["plugin_key"]):
                self.assertEqual("start" in events, "stop" in events, "start and stop belong together")
                if not {"start", "stop"} <= events:
                    continue
                module_name = ".".join(manifest_path.parent.relative_to(SOFTWARE).parts) + ".plugin"
                plugin = importlib.import_module(module_name).PLUGIN
                self.assertIsNotNone(plugin.on_trial_start, "an actuator implements on_trial_start")
                self.assertIsNotNone(plugin.on_trial_stop, "an actuator implements on_trial_stop")

    def test_no_plugin_keeps_its_own_per_card_start_stop_switch(self) -> None:
        # The card's actuator selection is the one switch; a plugin-specific
        # boolean that also gates start/stop would be a second, hidden list.
        for key, manifest in get_plugin_manifests().items():
            schema = manifest.get("card_actions_schema") or {}
            with self.subTest(plugin=key):
                self.assertNotIn("forward_marker", schema)
                self.assertNotIn("to_touchdesigner", schema)

    def test_shipped_actuators_and_sensors(self) -> None:
        actuators = set(stimulus_actuator_keys())
        self.assertIn("osc", actuators)
        self.assertIn("am_hub", actuators, "the AM Hub also drives actuators")
        for sensor in ("brainbit", "mini_radar", "camera_emotion"):
            self.assertNotIn(sensor, actuators, f"{sensor} is a sensor and only receives markers")

    def test_detection_reads_only_the_manifest(self) -> None:
        self.assertTrue(is_stimulus_actuator(_actuator_manifest()))
        self.assertFalse(is_stimulus_actuator(_sensor_manifest()))
        self.assertFalse(is_stimulus_actuator({"runtime": {"trial_events": ["start"]}}))
        self.assertFalse(is_stimulus_actuator(None))

    def test_new_core_paths_name_no_plugin(self) -> None:
        plugin_keys = {
            key
            for key, manifest in get_plugin_manifests().items()
            if manifest.get("category") != "card"
        }
        sources = {
            "registry actuator helpers": "\n".join(
                inspect.getsource(function)
                for function in (
                    registry.is_stimulus_actuator,
                    registry.stimulus_actuator_keys,
                    registry._selected_actuators,
                    registry._run_trial_callbacks,
                )
            ),
            "actuator normalization": inspect.getsource(study_plugin_config.normalize_card_actuator_plugins),
        }
        ui = SOFTWARE / "study_runner" / "apps" / "ui" / "scripts"
        for relative in (
            "participant/participant-stimulus-execution.js",
            "shared/plugin-catalog.js",
        ):
            sources[relative] = (ui / relative).read_text(encoding="utf-8")
        for name, source in sources.items():
            for key in plugin_keys:
                with self.subTest(source=name, plugin=key):
                    self.assertNotIn(f'"{key}"', source)
                    self.assertNotIn(f"'{key}'", source)
        # The pre-v3 card-action mapping lost its OSC and BrainBit branches;
        # its one remaining (camera) branch predates this rule.
        legacy = inspect.getsource(study_plugin_config._apply_legacy_card_actions)
        self.assertNotIn('"osc"', legacy)
        self.assertNotIn('"brainbit"', legacy)


class ActuatorDispatchTests(unittest.TestCase):
    def _dispatch(self, runner, options):
        calls: list[str] = []
        plugins = tuple(
            Plugin(
                key, key, "output", key,
                on_trial_start=lambda _c, _o, key=key: calls.append(f"start:{key}"),
                on_trial_stop=lambda _c, _o, key=key: calls.append(f"stop:{key}"),
                on_trial_marker=lambda _c, _o, key=key: calls.append(f"marker:{key}"),
            )
            for key in ("first", "second")
        )
        with (
            patch.object(registry, "PLUGINS", plugins),
            patch.object(registry, "_is_config_enabled", return_value=True),
        ):
            runner(options, object())
        return calls

    def test_start_and_stop_reach_only_the_selected_actuators(self) -> None:
        self.assertEqual(self._dispatch(run_trial_start, {"actuator_plugins": ["second"]}), ["start:second"])
        self.assertEqual(self._dispatch(run_trial_stop, {"actuator_plugins": ["second"]}), ["stop:second"])
        self.assertEqual(self._dispatch(run_trial_start, {"actuator_plugins": []}), [])

    def test_an_event_without_a_selection_keeps_reaching_every_plugin(self) -> None:
        self.assertEqual(self._dispatch(run_trial_start, {}), ["start:first", "start:second"])

    def test_markers_are_not_filtered_by_the_actuator_selection(self) -> None:
        self.assertEqual(
            self._dispatch(run_trial_marker, {"actuator_plugins": []}),
            ["marker:first", "marker:second"],
        )


class ActuatorSelectionNormalizationTests(unittest.TestCase):
    MANIFESTS = {"lamp": _actuator_manifest(), "radar": _sensor_manifest()}

    def test_a_card_from_before_the_selection_keeps_every_actuator(self) -> None:
        self.assertEqual(normalize_card_actuator_plugins({}, manifests=self.MANIFESTS), ["lamp"])

    def test_a_card_from_before_the_selection_keeps_its_old_switch_offs(self) -> None:
        manifests = {**self.MANIFESTS, "fan": _actuator_manifest()}
        self.assertEqual(normalize_card_actuator_plugins({"send_signal": False}, manifests=manifests), [])
        self.assertEqual(
            normalize_card_actuator_plugins({"plugin_actions": {"lamp": {"any_option": False}}}, manifests=manifests),
            ["fan"],
            "a plugin's own boolean card option set to false switched it off",
        )

    def test_an_explicit_selection_is_kept_and_cleaned(self) -> None:
        self.assertEqual(normalize_card_actuator_plugins({"actuator_plugins": []}, manifests=self.MANIFESTS), [])
        self.assertEqual(
            normalize_card_actuator_plugins(
                {"actuator_plugins": ["lamp", "lamp", "radar", "absent_plugin"]},
                manifests=self.MANIFESTS,
            ),
            ["lamp", "absent_plugin"],
            "duplicates and installed sensors drop out; a missing plugin's key survives",
        )

    def test_an_invalid_selection_is_rejected(self) -> None:
        with self.assertRaises(PluginConfigError):
            normalize_card_actuator_plugins({"actuator_plugins": "lamp"}, manifests=self.MANIFESTS)
        with self.assertRaises(PluginConfigError):
            normalize_card_actuator_plugins({"actuator_plugins": [""]}, manifests=self.MANIFESTS)


class StimulusCardFieldsTests(unittest.TestCase):
    HOST = {"plugin_actions": {}, "actuator_plugins": ["lamp"]}

    def test_defaults_keep_todays_behaviour(self) -> None:
        card = stimulus_card.normalize_card_config("stimulus", {"type": "stimulus"}, 1, self.HOST)
        self.assertTrue(card["auto_advance"])
        self.assertEqual(card["end_sound"], "none")
        self.assertEqual(card["actuator_plugins"], ["lamp"])
        self.assertEqual(card["overtime_max_ms"], 300_000)
        self.assertFalse(card["overtime_keep_actuators"])
        self.assertEqual(stimulus_card.get_card_defaults("stimulus")["actuator_plugins"], [])

    def test_end_of_time_fields_are_bounded(self) -> None:
        card = stimulus_card.normalize_card_config(
            "stimulus",
            {"end_sound": "custom", "end_sound_url": " https://x/sound.mp3 ", "end_sound_volume": 40,
             "auto_advance": False, "overtime_keep_stimulus": False, "overtime_keep_actuators": True,
             "overtime_max_ms": 60_000},
            2,
            self.HOST,
        )
        self.assertEqual(card["end_sound_url"], "https://x/sound.mp3")
        self.assertFalse(card["auto_advance"])
        self.assertFalse(card["overtime_keep_stimulus"])
        self.assertTrue(card["overtime_keep_actuators"])
        for invalid in ({"end_sound": "siren"}, {"end_sound_volume": 101}, {"overtime_max_ms": 5_000}):
            with self.subTest(invalid=invalid), self.assertRaises(CardValidationError):
                stimulus_card.normalize_card_config("stimulus", invalid, 3, self.HOST)


class OvertimeSafetyDeadlineTests(unittest.TestCase):
    BASE = {
        "event_id": "start-1",
        "stop_event_id": "stop-1",
        "stimulus_id": "stimulus-1",
        "planned_start_epoch_ms": 100_500.0,
        "planned_deadline_epoch_ms": 101_000.0,
    }

    def test_without_overtime_the_stop_at_the_planned_deadline_is_unchanged(self) -> None:
        deadline, identity = stop_deadline_for(self.BASE)
        self.assertEqual(deadline, 101_000.0)
        self.assertEqual(identity["event_id"], "stop-1")
        self.assertEqual(identity["marker_event"], "stimulus_active_stop")

    def test_actuators_that_stop_at_time_up_are_stopped_by_the_time_up_event(self) -> None:
        deadline, identity = stop_deadline_for(
            {**self.BASE, "actuator_stop_at": "time_up", "time_up_event_id": "time-up-1"}
        )
        self.assertEqual(deadline, 101_000.0)
        self.assertEqual(identity["event_id"], "time-up-1")
        self.assertEqual(identity["marker_event"], "stimulus_time_up")

    def test_actuators_that_run_through_overtime_stop_at_the_maximum(self) -> None:
        deadline, identity = stop_deadline_for({**self.BASE, "stop_deadline_epoch_ms": 401_000.0})
        self.assertEqual(deadline, 401_000.0)
        self.assertEqual(identity["event_id"], "stop-1")

    def test_invalid_end_of_time_fields_are_rejected(self) -> None:
        for invalid in (
            {"stop_deadline_epoch_ms": 100_000.0},
            {"stop_deadline_epoch_ms": 101_000.0 + 3_600_001.0},
            {"actuator_stop_at": "time_up"},
            {"actuator_stop_at": "time_up", "time_up_event_id": "t", "stop_deadline_epoch_ms": 102_000.0},
            {"actuator_stop_at": "later"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                stop_deadline_for({**self.BASE, **invalid})

    def test_prepare_and_start_must_agree_on_how_the_stimulus_ends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(Path(temp_dir), clock=lambda: 100.0, scheduling_enabled=False)
            payload = {**self.BASE, "stop_deadline_epoch_ms": 401_000.0, "actuator_plugins": ["lamp"]}
            service.prepare(payload)
            deadline, identity = stop_deadline_for(payload)
            armed = service.arm_deadline("stimulus-1", deadline, {**payload, **identity}, lambda _p: {})
            self.assertEqual(armed["deadline_epoch_ms"], 401_000.0)
            self.assertEqual(service.authorize_start(payload)["source"], "prepared")
            from study_runner.runtime_core.studies.trial_event_service import TrialEventConflictError

            with self.assertRaises(TrialEventConflictError):
                service.authorize_start({**payload, "actuator_plugins": []})

    def test_the_armed_stop_carries_the_time_up_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(Path(temp_dir), clock=lambda: 100.0, scheduling_enabled=False)
            payload = {**self.BASE, "actuator_stop_at": "time_up", "time_up_event_id": "time-up-1"}
            service.prepare(payload)
            deadline, identity = stop_deadline_for(payload)
            armed = service.arm_deadline("stimulus-1", deadline, {**payload, **identity}, lambda _p: {})
        self.assertEqual(armed["event_id"], "time-up-1")
        self.assertEqual(armed["stop_payload"]["marker_event"], "stimulus_time_up")


class OvertimeWindowsTests(unittest.TestCase):
    class Reader:
        def __init__(self, markers):
            self.markers = markers

        def read_streams(self, _path):
            return [
                {
                    "stream_key": "markers",
                    "timestamps": [timestamp for timestamp, _ in self.markers],
                    "samples": [{"event": f"marker|event_id={event_id}"} for _, event_id in self.markers],
                },
                {
                    "stream_key": "sensor",
                    "nominal_rate_hz": 1,
                    "timestamps": [100.0, 101.0, 102.0, 103.0, 104.0],
                    "samples": [{"value": value} for value in (1, 1, 5, 5, 5)],
                },
            ]

    EVENT = {
        "question_index": 1,
        "question_type": "stimulus",
        "start_event_id": "start-1",
        "time_up_event_id": "time-up-1",
        "stop_event_id": "stop-1",
    }

    def _build(self, markers):
        with tempfile.TemporaryDirectory() as temp_dir:
            merged = Path(temp_dir) / "session.xdf"
            merged.write_bytes(b"fixture")
            return CardSummaryBuilder(self.Reader(markers)).build(merged, [self.EVENT], session_id="s")

    def test_the_card_window_ends_at_time_up_and_overtime_has_its_own(self) -> None:
        summary = self._build([(100.0, "start-1"), (102.0, "time-up-1"), (105.0, "stop-1")])
        card, overtime = summary["cards"]
        self.assertEqual((card["window"], card["start_epoch"], card["end_epoch"]), ("card", 100.0, 102.0))
        self.assertEqual(card["streams"]["sensor"]["channels"]["value"]["mean"], 1)
        self.assertEqual((overtime["window"], overtime["start_epoch"], overtime["end_epoch"]), ("overtime", 102.0, 105.0))
        self.assertEqual(overtime["streams"]["sensor"]["channels"]["value"]["mean"], 5)
        self.assertEqual(overtime["question_index"], 1)

    def test_a_missing_end_of_overtime_is_a_warning_not_a_failure(self) -> None:
        summary = self._build([(100.0, "start-1"), (102.0, "time-up-1")])
        self.assertEqual([card["window"] for card in summary["cards"]], ["card"])
        codes = [warning["code"] for warning in summary.get("quality_warnings") or []]
        self.assertIn("overtime_window_unavailable", codes)

    def test_answer_details_report_the_overtime(self) -> None:
        details = results_service.build_answer_details(
            {
                "participant_id": "p01",
                "timestamp_start": "2026-10-02T10:00:00Z",
                "timestamp_end": "2026-10-02T10:01:00Z",
                "card_events": [
                    {
                        "question_index": 0,
                        "question_type": "stimulus",
                        "shown_at": "2026-10-02T10:00:00Z",
                        "active_started_at": "2026-10-02T10:00:01Z",
                        "time_up_at": "2026-10-02T10:00:31Z",
                        "active_ended_at": "2026-10-02T10:00:43Z",
                        "overtime_ms": 12_000,
                    }
                ],
            },
            {"questions": [{"type": "stimulus"}]},
            {},
        )
        self.assertEqual(details[0]["interval_seconds"], 30.0, "the card's own interval ends at time-up")
        self.assertEqual(details[0]["overtime_seconds"], 12.0)
        self.assertEqual(details[0]["time_up_at"], "2026-10-02T10:00:31Z")


if __name__ == "__main__":
    unittest.main()
