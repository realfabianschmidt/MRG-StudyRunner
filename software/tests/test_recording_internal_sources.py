"""markers and clock_diagnostics: the two recording sources every session
carries, whether or not the study asked for them.

They used to be plugins (`test_clock_diagnostics_plugin.py`, now gone) until
`tests/test_plugin_removability.py` proved that was the wrong shape: nothing
worked with either one removed. They are core recording code now -- see
`recording/markers.py` and `recording/clock_diagnostics.py` -- called directly
from `trial_service.py` rather than through the generic plugin dispatch.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.recording import clock_diagnostics, markers


class _Outlet:
    def __init__(self) -> None:
        self.samples: list = []

    def push_sample(self, sample, timestamp=None) -> None:
        if timestamp is None:
            self.samples.append(list(sample))
        else:
            self.samples.append((list(sample), float(timestamp)))


class MarkersTests(unittest.TestCase):
    def tearDown(self) -> None:
        markers.stop()

    def test_send_marker_does_nothing_before_initialize(self) -> None:
        result = markers.send_marker("study:start")  # must not raise
        self.assertEqual(markers.status()["status"], "waiting")
        self.assertFalse(result["sent"])

    def test_send_marker_pushes_the_value_once_active(self) -> None:
        outlet = _Outlet()
        markers._outlet = outlet

        result = markers.send_marker("study:start")

        self.assertEqual(outlet.samples, [["study:start"]])
        self.assertEqual(markers.status()["status"], "enabled")
        self.assertTrue(result["sent"])
        self.assertIsNone(result["marker_lsl_timestamp"])
        self.assertIsNotNone(result["marker_push_epoch_ms"])

    def test_explicit_server_time_uses_stable_monotonic_mapping_and_order(self) -> None:
        outlet = _Outlet()
        markers._outlet = outlet
        markers._wall_to_lsl_offset = -900.0

        first = markers.send_marker("first", server_epoch_ms=1_000_000.0)
        second = markers.send_marker("older-retry", server_epoch_ms=999_000.0)

        self.assertEqual(outlet.samples[0], (["first"], 100.0))
        self.assertEqual(outlet.samples[1], (["older-retry"], 100.0))
        self.assertEqual(first["marker_lsl_timestamp"], 100.0)
        self.assertEqual(second["marker_lsl_timestamp"], 100.0)
        self.assertGreaterEqual(second["marker_push_epoch_ms"], first["marker_push_epoch_ms"])


class ClockDiagnosticsTests(unittest.TestCase):
    def tearDown(self) -> None:
        clock_diagnostics.stop()

    def test_event_contains_wall_lsl_and_client_clock_observations(self) -> None:
        outlet = _Outlet()
        clock_diagnostics._outlet = outlet
        clock_diagnostics._local_clock = lambda: 42.5
        clock_diagnostics._event_sequence = 0

        clock_diagnostics.emit(
            {
                "server_received_epoch_ms": 1_760_000_000_123.0,
                "clock_offset_ms": 12.5,
                "clock_sync_rtt_ms": 24.0,
                "sequence_number": 9,
                "source_epoch_ms": 1_760_000_000_100.0,
            }
        )

        sample, timestamp = outlet.samples[0]
        self.assertEqual(timestamp, 42.5)
        self.assertEqual(
            sample,
            [1_760_000_000_123.0, 42.5, 12.5, 24.0, 9.0, 1_760_000_000_100.0, 1.0],
        )


class TrialServiceDispatchTests(unittest.TestCase):
    """Both sources are called directly from trial_service.py now, not through
    the generic plugin dispatch -- confirm every trial event still reaches them,
    with no hardware config and no toggle involved."""

    def test_trial_start_stop_and_marker_all_reach_both_sources(self) -> None:
        from study_runner.backend.services.studies import trial_service

        with (
            patch.object(markers, "send_marker") as send_marker,
            patch.object(clock_diagnostics, "emit") as emit,
            patch.object(
                trial_service,
                "run_trial_start",
                side_effect=lambda _options, _context, prior: prior,
            ),
            patch.object(
                trial_service,
                "run_trial_stop",
                side_effect=lambda _options, _context, prior: prior,
            ),
            patch.object(
                trial_service,
                "run_trial_marker",
                side_effect=lambda _options, _context, prior: prior,
            ),
        ):
            trial_service.start_trial_session({"study_id": "s", "participant_id": "p"})
            trial_service.stop_trial_session({"study_id": "s", "participant_id": "p"})
            trial_service.send_trial_marker("card_shown", {"study_id": "s", "participant_id": "p"})

        self.assertEqual(send_marker.call_count, 3)
        self.assertEqual(emit.call_count, 3)

    def test_one_source_failing_attempts_the_other_but_fails_the_durable_event(self) -> None:
        from study_runner.backend.services.studies import trial_service

        with (
            patch.object(markers, "send_marker", side_effect=RuntimeError("outlet gone")),
            patch.object(clock_diagnostics, "emit") as emit,
        ):
            with self.assertRaises(trial_service.TrialDispatchError) as raised:
                trial_service.start_trial_session({"study_id": "s", "participant_id": "p"})

        emit.assert_called_once()
        self.assertFalse(raised.exception.outcomes["core.markers"]["ok"])
        self.assertTrue(raised.exception.outcomes["core.clock_diagnostics"]["ok"])

    def test_core_marker_with_explicit_source_time_precedes_plugin_callbacks(self) -> None:
        from study_runner.backend.services.studies import trial_service

        order: list[str] = []

        def plugin_dispatch(_options, _context, prior):
            order.append("plugin")
            return prior

        def push_marker(*_args, **_kwargs):
            order.append("marker")
            return {
                "sent": True,
                "marker_lsl_timestamp": 42.125,
                "marker_push_epoch_ms": 1_760_000_000_130.0,
            }

        with (
            patch.object(markers, "send_marker", side_effect=push_marker) as marker,
            patch.object(clock_diagnostics, "emit", side_effect=lambda *_args: order.append("clock")),
            patch.object(trial_service, "run_trial_start", side_effect=plugin_dispatch),
        ):
            response = trial_service.start_trial_session(
                {
                    "study_id": "s",
                    "participant_id": "p",
                    "client_trigger_epoch_ms": 1_760_000_000_100.0,
                    "server_received_epoch_ms": 1_760_000_000_125.0,
                }
            )

        self.assertEqual(order, ["marker", "clock", "plugin"])
        self.assertEqual(marker.call_args.kwargs["server_epoch_ms"], 1_760_000_000_100.0)
        self.assertEqual(response["marker_lsl_timestamp"], 42.125)
        self.assertEqual(response["marker_push_epoch_ms"], 1_760_000_000_130.0)
        self.assertEqual(response["dispatch"]["core.markers"]["marker_lsl_timestamp"], 42.125)

    def test_actual_clamped_lsl_timestamp_and_post_push_walltime_reach_journal(self) -> None:
        from study_runner.backend.services.studies import trial_service
        from study_runner.backend.services.studies.trial_event_service import TrialEventService

        outlet = _Outlet()
        markers._outlet = outlet
        markers._local_clock = None
        markers._wall_to_lsl_offset = -900.0
        markers._last_lsl_timestamp = 100.0

        # Before push the wall clock is 1000.0; the fake outlet records the
        # sample, after which send_marker observes 1000.25. This proves that the
        # persisted wall time describes the completed push rather than ingress.
        def wall_time() -> float:
            return 1000.25 if outlet.samples else 1000.0

        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrialEventService(
                Path(temp_dir),
                clock=lambda: 2000.0,
                scheduling_enabled=False,
            )
            payload = {
                "event_id": "marker-actual-time",
                "source_epoch_ms": 999_000.0,
                "server_received_epoch_ms": 999_500.0,
            }
            with (
                patch.object(markers.time, "time", side_effect=wall_time),
                patch.object(clock_diagnostics, "emit"),
                patch.object(
                    trial_service,
                    "run_trial_marker",
                    side_effect=lambda _options, _context, prior: prior,
                ),
            ):
                response = service.execute(
                    payload["event_id"],
                    "trial_marker",
                    payload,
                    lambda options: trial_service.send_trial_marker("question_shown", options),
                )
            record = service.snapshot()["events"][payload["event_id"]]

        # Source time maps to 99.0, but the actual outlet timestamp is clamped
        # to 100.0. Every public/durable layer must expose that used value.
        self.assertEqual(outlet.samples[0][1], 100.0)
        self.assertEqual(response["marker_lsl_timestamp"], 100.0)
        self.assertEqual(response["marker_push_epoch_ms"], 1_000_250.0)
        self.assertEqual(record["response"]["marker_lsl_timestamp"], 100.0)
        self.assertEqual(
            record["component_outcomes"]["core.markers"]["marker_lsl_timestamp"],
            100.0,
        )
        self.assertEqual(
            record["component_outcomes"]["core.markers"]["marker_push_epoch_ms"],
            1_000_250.0,
        )


if __name__ == "__main__":
    unittest.main()
