"""Persistence, rehydrate, and staleness rules for SessionStore."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.studies.session_store import STALE_AFTER_SECONDS, SessionStore, public_session


class ManualClock:
    def __init__(self, value: float = 1_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def session_payload(**overrides) -> dict:
    payload = {
        "study_id": "study-a",
        "participant_id": "p01",
        "client_id": "tablet-1",
        "current_index": 2,
        "current_type": "likert",
    }
    payload.update(overrides)
    return payload


class SessionStoreTests(unittest.TestCase):
    def test_start_creates_active_session_persisted_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            session = store.start_or_reuse(session_payload())

            self.assertEqual(session["status"], "active")
            self.assertFalse(session["reused"])
            self.assertTrue(store.path.is_file())

    def test_second_start_for_same_participant_reuses_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            first = store.start_or_reuse(session_payload())
            second = store.start_or_reuse(session_payload(current_index=5))

            self.assertEqual(first["session_id"], second["session_id"])
            self.assertTrue(second["reused"])

    def test_rehydrate_recovers_session_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            first = SessionStore(data_dir)
            started = first.start_or_reuse(session_payload())

            second = SessionStore(data_dir)
            found = second.find_active("study-a", "p01", "tablet-1")

            self.assertIsNotNone(found)
            self.assertEqual(found["session_id"], started["session_id"])

    def test_session_older_than_stale_window_is_marked_stale_on_rehydrate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            clock = ManualClock()
            first = SessionStore(data_dir, clock=clock)
            first.start_or_reuse(session_payload())

            clock.value += STALE_AFTER_SECONDS + 1
            second = SessionStore(data_dir, clock=clock)

            self.assertIsNone(second.find_active("study-a", "p01", "tablet-1"))

    def test_resume_by_session_id_works_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            first = SessionStore(data_dir)
            started = first.start_or_reuse(session_payload())

            second = SessionStore(data_dir)
            resumed = second.resume({"session_id": started["session_id"], "event": "study_resume_after_reload"})

            self.assertIsNotNone(resumed)
            self.assertEqual(resumed["status"], "active")

    def test_resume_by_session_id_requires_matching_identity_when_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            started = store.start_or_reuse(session_payload())

            self.assertIsNone(store.resume({"session_id": started["session_id"], "study_id": "other-study"}))
            self.assertIsNone(store.resume({"session_id": started["session_id"], "participant_id": "p02"}))
            self.assertIsNone(store.resume({"session_id": started["session_id"], "client_id": "tablet-2"}))

            resumed = store.resume(
                {
                    "session_id": started["session_id"],
                    "study_id": "study-a",
                    "participant_id": "p01",
                    "client_id": "tablet-1",
                }
            )

        self.assertIsNotNone(resumed)
        self.assertEqual(resumed["session_id"], started["session_id"])

    def test_resume_refuses_a_stale_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            clock = ManualClock()
            first = SessionStore(data_dir, clock=clock)
            started = first.start_or_reuse(session_payload())

            clock.value += STALE_AFTER_SECONDS + 1
            second = SessionStore(data_dir, clock=clock)

            self.assertIsNone(second.resume({"session_id": started["session_id"]}))

    def test_resume_records_interruption_when_stimulus_was_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            started = store.start_or_reuse(session_payload())

            resumed = store.resume(
                {
                    "session_id": started["session_id"],
                    "event": "pagehide",
                    "is_stimulus_active": True,
                }
            )

            self.assertIsNotNone(resumed["last_interruption"])
            self.assertTrue(resumed["last_interruption"]["interrupted_by_reload"])

    def test_mark_completed_removes_session_from_active_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            started = store.start_or_reuse(session_payload())

            self.assertTrue(store.mark_completed(started["session_id"]))
            self.assertEqual(store.list_active(), [])
            self.assertIsNone(store.find_active("study-a", "p01", "tablet-1"))

    def test_mark_completed_unknown_session_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            self.assertFalse(store.mark_completed("does-not-exist"))

    def test_find_active_requires_matching_client_id_when_given(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            store.start_or_reuse(session_payload(client_id="tablet-1"))

            self.assertIsNone(store.find_active("study-a", "p01", "tablet-2"))
            self.assertIsNotNone(store.find_active("study-a", "p01", "tablet-1"))

    def test_list_active_reflects_started_at_epoch_for_flush_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = ManualClock()
            store = SessionStore(Path(temp_dir), clock=clock)
            store.start_or_reuse(session_payload())

            [active] = store.list_active()
            self.assertEqual(active["started_at_epoch"], clock.value)

    def test_mutating_a_returned_session_does_not_affect_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            session = store.start_or_reuse(session_payload())
            session["status"] = "tampered"

            self.assertEqual(store.get(session["session_id"])["status"], "active")

    def test_public_session_strips_internal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionStore(Path(temp_dir))
            session = store.start_or_reuse(session_payload())

            public = public_session(session)

            self.assertNotIn("started_at_epoch", public)
            self.assertNotIn("events", public)
            self.assertEqual(public["session_id"], session["session_id"])

    def test_public_session_of_none_is_none(self) -> None:
        self.assertIsNone(public_session(None))


if __name__ == "__main__":
    unittest.main()
