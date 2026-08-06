from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.studies.trial_event_service import (
    TrialEventConflictError,
    TrialEventService,
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


if __name__ == "__main__":
    unittest.main()
