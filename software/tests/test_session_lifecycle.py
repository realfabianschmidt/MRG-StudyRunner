"""Package 5a: the explicit session lifecycle and its documented mapping.

The three underlying state machines stay separate on purpose, so these
tests are mostly about the *mapping* being honest -- especially the two
rules the module exists to protect: an upload never un-seals validated
data, and SEALED means the data validated rather than merely that a job
stopped running.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.session_lifecycle import (
    FAILED,
    FINALIZING,
    IDLE,
    PREFLIGHT,
    RECORDING,
    SEALED,
    WITHDRAWN,
    SessionLifecycleError,
    derive_session_lifecycle,
    is_allowed_transition,
    require_transition,
)


class DeriveSessionLifecycleTests(unittest.TestCase):
    def test_nothing_started_is_idle(self) -> None:
        self.assertEqual(derive_session_lifecycle(), IDLE)

    def test_recording_plan_states_map_to_the_recording_phase(self) -> None:
        self.assertEqual(derive_session_lifecycle(recording_plan={"status": "starting"}), PREFLIGHT)
        self.assertEqual(derive_session_lifecycle(recording_plan={"status": "recording"}), RECORDING)
        self.assertEqual(derive_session_lifecycle(recording_plan={"status": "recovering"}), RECORDING)
        self.assertEqual(derive_session_lifecycle(recording_plan={"status": "frozen"}), FINALIZING)

    def test_recorder_attention_stays_in_the_recording_phase(self) -> None:
        """The reason stays in the plan's own status; participation continues."""
        self.assertEqual(
            derive_session_lifecycle(recording_plan={"status": "attention_required"}),
            RECORDING,
        )

    def test_finalization_outranks_the_recording_plan(self) -> None:
        lifecycle = derive_session_lifecycle(
            recording_plan={"status": "frozen"},
            finalization_state={"status": "completed", "quality_status": "valid"},
        )
        self.assertEqual(lifecycle, SEALED)

    def test_a_human_confirmed_degraded_result_is_still_sealed(self) -> None:
        lifecycle = derive_session_lifecycle(
            finalization_state={"status": "completed_degraded", "quality_status": "degraded"},
        )
        self.assertEqual(lifecycle, SEALED)

    def test_invalid_data_is_never_sealed_even_if_the_job_reports_completed(self) -> None:
        """SEALED describes validated data, not merely a job that stopped."""
        lifecycle = derive_session_lifecycle(
            finalization_state={"status": "completed", "quality_status": "invalid"},
        )
        self.assertEqual(lifecycle, FINALIZING)

    def test_attention_required_finalization_is_still_finalizing(self) -> None:
        """It is not terminal: an operator can retry or confirm degraded."""
        lifecycle = derive_session_lifecycle(
            finalization_state={"status": "attention_required", "quality_status": "invalid"},
        )
        self.assertEqual(lifecycle, FINALIZING)

    def test_failed_finalization_is_failed(self) -> None:
        self.assertEqual(derive_session_lifecycle(finalization_state={"status": "failed"}), FAILED)

    def test_withdrawal_wins_over_every_other_state(self) -> None:
        lifecycle = derive_session_lifecycle(
            recording_plan={"status": "recording"},
            finalization_state={"status": "completed", "quality_status": "valid"},
            withdrawn=True,
        )
        self.assertEqual(lifecycle, WITHDRAWN)

    def test_an_unknown_status_is_reported_as_idle_not_guessed_at(self) -> None:
        self.assertEqual(derive_session_lifecycle(recording_plan={"status": "teleporting"}), IDLE)

    def test_an_archival_session_with_only_a_marker_is_not_reported_as_idle(self) -> None:
        """A session recorded before this machinery existed still carries
        COMPLETE.json; calling something visibly finished "IDLE" would be a
        confidently wrong answer."""
        lifecycle = derive_session_lifecycle(terminal_marker={"status": "completed"})
        self.assertEqual(lifecycle, SEALED)

    def test_an_attention_marker_without_finalization_state_is_finalizing(self) -> None:
        lifecycle = derive_session_lifecycle(terminal_marker={"status": "attention_required"})
        self.assertEqual(lifecycle, FINALIZING)

    def test_live_finalization_state_outranks_a_stale_marker(self) -> None:
        lifecycle = derive_session_lifecycle(
            finalization_state={"status": "retrying"},
            terminal_marker={"status": "completed"},
        )
        self.assertEqual(lifecycle, FINALIZING)

    def test_upload_status_is_not_an_input_at_all(self) -> None:
        """A destination that will not accept a file cannot un-seal data."""
        sealed = {"status": "completed", "quality_status": "valid"}
        self.assertEqual(derive_session_lifecycle(finalization_state=sealed), SEALED)
        self.assertEqual(
            derive_session_lifecycle(finalization_state={**sealed, "upload_status": "failed"}),
            SEALED,
        )


class TransitionGuardTests(unittest.TestCase):
    def test_the_ordinary_path_is_allowed_end_to_end(self) -> None:
        path = [IDLE, PREFLIGHT, RECORDING, FINALIZING, SEALED]
        for current, following in zip(path, path[1:]):
            self.assertTrue(is_allowed_transition(current, following), f"{current} -> {following}")

    def test_a_sealed_session_never_reopens_for_finalization(self) -> None:
        self.assertFalse(is_allowed_transition(SEALED, FINALIZING))
        with self.assertRaises(SessionLifecycleError):
            require_transition(SEALED, FINALIZING)

    def test_withdrawal_is_reachable_during_recording_and_after_sealing(self) -> None:
        self.assertTrue(is_allowed_transition(RECORDING, WITHDRAWN))
        self.assertTrue(is_allowed_transition(SEALED, WITHDRAWN))

    def test_withdrawal_is_terminal(self) -> None:
        for state in (IDLE, PREFLIGHT, RECORDING, FINALIZING, SEALED, FAILED):
            self.assertFalse(is_allowed_transition(WITHDRAWN, state), state)

    def test_a_failed_finalization_may_be_retried(self) -> None:
        self.assertTrue(is_allowed_transition(FAILED, FINALIZING))

    def test_staying_in_the_same_state_is_not_a_transition(self) -> None:
        require_transition(RECORDING, RECORDING)

    def test_skipping_the_recording_phase_is_rejected(self) -> None:
        with self.assertRaises(SessionLifecycleError):
            require_transition(IDLE, SEALED)


if __name__ == "__main__":
    unittest.main()
