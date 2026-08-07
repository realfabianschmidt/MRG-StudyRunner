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
        markers.send_marker("study:start")  # must not raise
        self.assertEqual(markers.status()["status"], "waiting")

    def test_send_marker_pushes_the_value_once_active(self) -> None:
        outlet = _Outlet()
        markers._outlet = outlet

        markers.send_marker("study:start")

        self.assertEqual(outlet.samples, [["study:start"]])
        self.assertEqual(markers.status()["status"], "enabled")


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
        ):
            trial_service.start_trial_session({"study_id": "s", "participant_id": "p"})
            trial_service.stop_trial_session({"study_id": "s", "participant_id": "p"})
            trial_service.send_trial_marker("card_shown", {"study_id": "s", "participant_id": "p"})

        self.assertEqual(send_marker.call_count, 3)
        self.assertEqual(emit.call_count, 3)

    def test_one_source_failing_does_not_stop_the_other_or_the_trial_event(self) -> None:
        from study_runner.backend.services.studies import trial_service

        with (
            patch.object(markers, "send_marker", side_effect=RuntimeError("outlet gone")),
            patch.object(clock_diagnostics, "emit") as emit,
        ):
            response = trial_service.start_trial_session({"study_id": "s", "participant_id": "p"})

        emit.assert_called_once()
        self.assertIn("marker_value", response)


if __name__ == "__main__":
    unittest.main()
