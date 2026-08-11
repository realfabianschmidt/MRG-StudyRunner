from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.studies.trial_event_service import (
    TrialEventConflictError,
    TrialEventService,
    TrialPreparationRequiredError,
)


class FakeTimer:
    created: list["FakeTimer"] = []

    def __init__(self, delay: float, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.__class__.created.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.callback()


class TrialEventServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeTimer.created = []

    def test_duplicate_event_returns_persisted_response_without_second_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(Path(temp_dir), scheduling_enabled=False)
            calls: list[str] = []

            def handler(payload):
                calls.append(payload["event_id"])
                return {"marker_value": "one"}

            first = service.execute("event-1", "trial_marker", {"event_id": "event-1"}, handler)
            duplicate = service.execute("event-1", "trial_marker", {"event_id": "event-1"}, handler)

            reloaded = TrialEventService(Path(temp_dir), scheduling_enabled=False)
            after_restart = reloaded.execute(
                "event-1", "trial_marker", {"event_id": "event-1"}, handler
            )

        self.assertEqual(calls, ["event-1"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertTrue(after_restart["duplicate"])
        self.assertEqual(after_restart["marker_value"], "one")

    def test_reusing_event_id_for_different_command_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(Path(temp_dir), scheduling_enabled=False)
            service.execute("event-1", "trial_marker", {"event_id": "event-1"}, lambda _payload: {})

            with self.assertRaises(TrialEventConflictError):
                service.execute(
                    "event-1",
                    "trial_marker",
                    {"event_id": "event-1", "question_index": 2},
                    lambda _payload: {},
                )

    def test_process_crash_releases_processing_event_for_same_id_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(Path(temp_dir), scheduling_enabled=False)

            def crash_after_side_effect(_payload):
                raise KeyboardInterrupt("simulated process termination")

            with self.assertRaises(KeyboardInterrupt):
                service.execute(
                    "event-crash",
                    "trial_marker",
                    {"event_id": "event-crash"},
                    crash_after_side_effect,
                )

            reloaded = TrialEventService(Path(temp_dir), scheduling_enabled=False)
            self.assertEqual(
                reloaded.snapshot()["events"]["event-crash"]["status"],
                "reconcile_required",
            )
            reconciled = reloaded.execute(
                "event-crash",
                "trial_marker",
                {"event_id": "event-crash"},
                lambda _payload: {"marker": "reconciled"},
            )

        self.assertEqual(reconciled["marker"], "reconciled")
        self.assertFalse(reconciled["duplicate"])

    def test_server_deadline_and_late_browser_stop_share_one_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 100.0,
                timer_factory=FakeTimer,
            )
            calls: list[dict] = []

            def handler(payload):
                calls.append(payload)
                return {"marker_value": "stopped"}

            stable = {
                "event_id": "stop-1",
                "stimulus_id": "stimulus-1",
                "study_id": "study",
                "participant_id": "p01",
                "question_index": 1,
                "question_type": "stimulus",
                "planned_deadline_epoch_ms": 101_000.0,
                "send_signal": True,
                "plugin_actions": {
                    "brainbit": {"to_touchdesigner": False},
                },
            }
            service.arm_deadline("stimulus-1", 101_000.0, stable, handler)
            self.assertEqual(len(FakeTimer.created), 1)
            self.assertAlmostEqual(FakeTimer.created[0].delay, 1.0)

            FakeTimer.created[0].fire()
            late_browser_payload = {
                **stable,
                "client_trigger_epoch_ms": 105_000.0,
                "automatic_deadline": False,
            }
            duplicate = service.execute("stop-1", "trial_stop", late_browser_payload, handler)

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["automatic_deadline"])
        self.assertTrue(duplicate["duplicate"])

    def test_cancelled_deadline_does_not_fire(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 100.0,
                timer_factory=FakeTimer,
            )
            calls: list[dict] = []
            service.arm_deadline(
                "stimulus-1",
                101_000.0,
                {"event_id": "stop-1"},
                lambda payload: calls.append(payload) or {},
            )
            self.assertTrue(service.cancel_deadline("stimulus-1"))
            FakeTimer.created[0].fire()

        self.assertEqual(calls, [])

    def test_prepare_arms_identity_contract_and_unprepared_start_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 100.0,
                timer_factory=FakeTimer,
            )
            payload = {
                "event_id": "start-1",
                "stop_event_id": "stop-1",
                "stimulus_id": "stimulus-1",
                "study_id": "study",
                "participant_id": "p01",
                "question_index": 1,
                "planned_start_epoch_ms": 100_500.0,
                "planned_deadline_epoch_ms": 101_000.0,
            }

            with self.assertRaises(TrialPreparationRequiredError):
                service.authorize_start(payload)

            service.prepare(payload)
            service.arm_deadline(
                "stimulus-1",
                101_000.0,
                {**payload, "event_id": "stop-1"},
                lambda _payload: {},
            )
            authorized = service.authorize_start(payload)

        self.assertEqual(authorized["source"], "prepared")

    def test_failed_deadline_stays_armed_and_retries_only_failed_components(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 100.0,
                timer_factory=FakeTimer,
            )
            calls: list[dict] = []

            class ComponentFailure(RuntimeError):
                def __init__(self, outcomes):
                    self.outcomes = outcomes
                    super().__init__("plugin failed")

            def handler(payload):
                calls.append(dict(payload))
                prior = payload.get("_trial_component_outcomes") or {}
                if not prior:
                    raise ComponentFailure(
                        {
                            "core.markers": {"ok": True},
                            "plugin.fixture": {"ok": False, "error": "offline"},
                        }
                    )
                return {
                    "dispatch": {
                        **prior,
                        "plugin.fixture": {"ok": True},
                    }
                }

            service.arm_deadline(
                "stimulus-1",
                101_000.0,
                {"event_id": "stop-1"},
                handler,
            )
            FakeTimer.created[0].fire()
            after_failure = service.snapshot()
            self.assertEqual(after_failure["deadlines"]["stimulus-1"]["status"], "armed")
            self.assertEqual(after_failure["events"]["stop-1"]["status"], "failed")
            self.assertEqual(len(FakeTimer.created), 2)

            FakeTimer.created[1].fire()
            after_retry = service.snapshot()

        self.assertEqual(after_retry["deadlines"]["stimulus-1"]["status"], "fired")
        self.assertEqual(after_retry["events"]["stop-1"]["status"], "done")
        self.assertTrue(calls[1]["_trial_component_outcomes"]["core.markers"]["ok"])

    def test_deadline_stop_waits_for_inflight_start_of_same_stimulus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 100.0,
                timer_factory=FakeTimer,
            )
            payload = {
                "event_id": "start-1",
                "stop_event_id": "stop-1",
                "stimulus_id": "stimulus-1",
                "planned_deadline_epoch_ms": 101_000.0,
            }
            entered_start = threading.Event()
            release_start = threading.Event()
            entered_stop = threading.Event()
            order: list[str] = []
            errors: list[BaseException] = []

            def start_handler(_payload):
                order.append("start_begin")
                entered_start.set()
                if not release_start.wait(2.0):
                    raise TimeoutError("test did not release start handler")
                order.append("start_end")
                return {}

            def stop_handler(_payload):
                order.append("stop")
                entered_stop.set()
                return {}

            service.arm_deadline(
                "stimulus-1",
                101_000.0,
                {**payload, "event_id": "stop-1"},
                stop_handler,
            )

            def run_start() -> None:
                try:
                    service.execute("start-1", "trial_start", payload, start_handler)
                except BaseException as error:  # surfaced below in the test thread
                    errors.append(error)

            start_thread = threading.Thread(target=run_start)
            deadline_thread = threading.Thread(target=FakeTimer.created[0].fire)
            start_thread.start()
            self.assertTrue(entered_start.wait(1.0))
            deadline_thread.start()
            self.assertFalse(entered_stop.wait(0.1))
            release_start.set()
            start_thread.join(2.0)
            deadline_thread.join(2.0)

            snapshot = service.snapshot()

        self.assertEqual(errors, [])
        self.assertFalse(start_thread.is_alive())
        self.assertFalse(deadline_thread.is_alive())
        self.assertEqual(order, ["start_begin", "start_end", "stop"])
        self.assertEqual(snapshot["events"]["start-1"]["status"], "done")
        self.assertEqual(snapshot["events"]["stop-1"]["status"], "done")
        self.assertEqual(snapshot["deadlines"]["stimulus-1"]["status"], "fired")

    def test_prepare_cancel_cannot_disarm_after_start_side_effects_begin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 100.0,
                timer_factory=FakeTimer,
            )
            payload = {
                "event_id": "start-1",
                "stop_event_id": "stop-1",
                "stimulus_id": "stimulus-1",
                "planned_deadline_epoch_ms": 101_000.0,
            }
            service.prepare(payload)
            service.arm_deadline(
                "stimulus-1",
                101_000.0,
                {**payload, "event_id": "stop-1"},
                lambda _payload: {},
            )
            entered_start = threading.Event()
            release_start = threading.Event()
            errors: list[BaseException] = []

            def start_handler(_payload):
                entered_start.set()
                if not release_start.wait(2.0):
                    raise TimeoutError("test did not release start handler")
                return {}

            def run_start() -> None:
                try:
                    service.execute("start-1", "trial_start", payload, start_handler)
                except BaseException as error:  # surfaced below in the test thread
                    errors.append(error)

            start_thread = threading.Thread(target=run_start)
            start_thread.start()
            self.assertTrue(entered_start.wait(1.0))
            with self.assertRaises(TrialEventConflictError):
                service.cancel_preparation("start-1", "stimulus-1", "abort")
            self.assertEqual(
                service.snapshot()["deadlines"]["stimulus-1"]["status"],
                "armed",
            )
            release_start.set()
            start_thread.join(2.0)

        self.assertFalse(start_thread.is_alive())
        self.assertEqual(errors, [])

    def test_cancelled_identity_cannot_be_prepared_or_rearmed_late(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 100.0,
                timer_factory=FakeTimer,
            )
            payload = {
                "event_id": "start-1",
                "stop_event_id": "stop-1",
                "stimulus_id": "stimulus-1",
                "planned_deadline_epoch_ms": 101_000.0,
            }
            service.cancel_preparation("start-1", "stimulus-1", "tablet_skip")

            with self.assertRaises(TrialEventConflictError):
                service.prepare(payload)
            with self.assertRaises(TrialEventConflictError):
                service.arm_deadline(
                    "stimulus-1",
                    101_000.0,
                    {**payload, "event_id": "stop-1"},
                    lambda _payload: {},
                )

            snapshot = service.snapshot()

        self.assertEqual(snapshot["preparations"], {})
        self.assertEqual(snapshot["deadlines"], {})

    def test_prepare_override_is_durable_and_requires_exact_identity_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(Path(temp_dir), clock=lambda: 100.0, scheduling_enabled=False)
            with self.assertRaises(ValueError):
                service.create_prepare_override("event-1", "stimulus-1", "")
            created = service.create_prepare_override(
                "event-1",
                "stimulus-1",
                "Local operator confirmed a lost prepare request.",
            )
            reloaded = TrialEventService(Path(temp_dir), clock=lambda: 100.0, scheduling_enabled=False)
            fetched = reloaded.get_prepare_override("event-1")

        self.assertFalse(created["duplicate"])
        self.assertEqual(fetched["stimulus_id"], "stimulus-1")
        self.assertEqual(fetched["scope"], "trial_prepare_only")

    def test_marker_timestamps_and_visual_onset_are_persisted_with_done_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(Path(temp_dir), clock=lambda: 100.0, scheduling_enabled=False)
            payload = {
                "event_id": "start-1",
                "source_epoch_ms": 99_900.0,
                "visual_onset_epoch_ms": 99_900.0,
                "onset_uncertainty_ms": 7.5,
                "server_received_epoch_ms": 99_925.0,
            }
            response = {
                "marker_lsl_timestamp": 42.125,
                "marker_push_epoch_ms": 99_930.0,
                "dispatch": {
                    "core.markers": {
                        "ok": True,
                        "marker_lsl_timestamp": 42.125,
                        "marker_push_epoch_ms": 99_930.0,
                    }
                },
            }

            service.execute("start-1", "trial_start", payload, lambda _payload: response)
            record = service.snapshot()["events"]["start-1"]

        self.assertEqual(record["source_epoch_ms"], 99_900.0)
        self.assertEqual(record["visual_onset_epoch_ms"], 99_900.0)
        self.assertEqual(record["onset_uncertainty_ms"], 7.5)
        self.assertEqual(record["response"]["marker_lsl_timestamp"], 42.125)
        self.assertEqual(
            record["component_outcomes"]["core.markers"]["marker_push_epoch_ms"],
            99_930.0,
        )


if __name__ == "__main__":
    unittest.main()
