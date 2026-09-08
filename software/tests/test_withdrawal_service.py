"""Package 5i: carrying out a consent withdrawal without overclaiming.

The cases that matter are the awkward ones: an interrupted run, a session
whose data was already published somewhere this process cannot reach, and
the question of what a withdrawn session looks like afterwards to someone
reading the session list.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.contracts.session_lifecycle import WITHDRAWN, WITHDRAWN_MARKER
from study_runner.runtime_core.delivery.upload_jobs_service import (
    UploadJobError,
    UploadJobService,
)
from study_runner.runtime_core.delivery.withdrawal_service import (
    WithdrawalError,
    WithdrawalService,
)
from study_runner.runtime_core.studies.session_journal_service import SessionJournalStore
from study_runner.runtime_core.studies.sessions_index_service import list_sessions


SESSION_ID = "20260908T101500Z-abcdef"


def _populate_session(root: Path) -> None:
    """A session tree with data in all the places a withdrawal must reach."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "result.json").write_text(
        json.dumps({"session_id": SESSION_ID, "answers": {"q1": "yes"}}), encoding="utf-8"
    )
    (root / "COMPLETE.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (root / "quality.jsonl").write_text('{"event":"summary"}\n', encoding="utf-8")
    raw = root / "raw" / "plugins" / "fixture"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "fixture.xdf").write_bytes(b"XDF")
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "worker.jsonl").write_text("{}\n", encoding="utf-8")


class WithdrawalServiceTests(unittest.TestCase):
    def test_the_data_goes_and_the_tombstone_stays(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session = data_dir / "sessions" / SESSION_ID
            _populate_session(session)

            service = WithdrawalService(data_dir)
            state = service.withdraw(
                session_id=SESSION_ID,
                session_root=session,
                reason="participant request",
                requested_by="operator",
            )

            self.assertEqual(state["status"], "withdrawn")
            self.assertTrue(session.is_dir())
            self.assertEqual([path.name for path in session.iterdir()], [WITHDRAWN_MARKER])
            marker = json.loads((session / WITHDRAWN_MARKER).read_text(encoding="utf-8"))
            self.assertEqual(marker["status"], "withdrawn")
            self.assertEqual(marker["session_id"], SESSION_ID)

    def test_the_ledger_lives_outside_the_folder_it_empties(self) -> None:
        """Otherwise an interrupted run erases its own record of progress."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session = data_dir / "sessions" / SESSION_ID
            _populate_session(session)
            service = WithdrawalService(data_dir)
            service.withdraw(session_id=SESSION_ID, session_root=session)

            ledger = service.load_state(SESSION_ID)
            self.assertIsNotNone(ledger)
            self.assertFalse(service.state_path(SESSION_ID).is_relative_to(session))
            self.assertEqual(ledger["steps"]["delete_session_contents"]["status"], "done")
            self.assertEqual(ledger["steps"]["delete_session_contents"]["details"]["removed"], 5)

    def test_an_interrupted_withdrawal_resumes_instead_of_restarting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session = data_dir / "sessions" / SESSION_ID
            _populate_session(session)

            attempts: list[str] = []

            def failing_stopper(session_id: str):
                attempts.append(session_id)
                raise RuntimeError("recorder is not responding")

            service = WithdrawalService(data_dir, recording_stopper=failing_stopper)
            with self.assertRaises(WithdrawalError):
                service.withdraw(session_id=SESSION_ID, session_root=session)

            # Nothing was deleted: the failure came before any deletion step.
            self.assertTrue((session / "result.json").is_file())
            self.assertEqual(service.load_state(SESSION_ID)["status"], "attention_required")

            resumed = WithdrawalService(data_dir, recording_stopper=lambda _id: {"stopped": True})
            state = resumed.withdraw(session_id=SESSION_ID, session_root=session)
            self.assertEqual(state["status"], "withdrawn")
            self.assertEqual(len(attempts), 1)

    def test_finishing_a_withdrawal_twice_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session = data_dir / "sessions" / SESSION_ID
            _populate_session(session)
            service = WithdrawalService(data_dir)
            service.withdraw(session_id=SESSION_ID, session_root=session)
            first = (session / WITHDRAWN_MARKER).read_text(encoding="utf-8")
            service.withdraw(session_id=SESSION_ID, session_root=session)
            self.assertEqual((session / WITHDRAWN_MARKER).read_text(encoding="utf-8"), first)

    def test_journal_copies_outside_the_session_folder_are_removed_too(self) -> None:
        """The copies a plain folder deletion would silently leave behind."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session = data_dir / "sessions" / SESSION_ID
            _populate_session(session)
            store = SessionJournalStore(data_dir)
            store.append("trial", SESSION_ID, "trial_completed", {"events": {"e1": {}}})
            journal = store.journal_path("trial", SESSION_ID)
            self.assertTrue(journal.is_file())

            WithdrawalService(data_dir, journal_store=store).withdraw(
                session_id=SESSION_ID, session_root=session
            )
            self.assertFalse(journal.is_file())

    def test_a_session_id_that_could_escape_its_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WithdrawalService(Path(temp_dir))
            for bad in ("../elsewhere", "", "  ", "a/b"):
                with self.assertRaises(WithdrawalError):
                    service.withdraw(session_id=bad, session_root=Path(temp_dir) / "x")


class WithdrawalAndUploadsTests(unittest.TestCase):
    def _service(self, data_dir: Path) -> UploadJobService:
        return UploadJobService(data_dir, clock=lambda: 1000.0)

    def test_pending_uploads_are_cancelled_and_their_payload_copies_deleted(self) -> None:
        """A queued job holds a second copy of the participant's data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            uploads = self._service(data_dir)
            uploads.enqueue(
                kind="notion",
                study_id="s",
                participant_id="p",
                session_id=SESSION_ID,
                label="Notion",
                payload={"answers": {"q1": "yes"}},
                job_id="job-1",
            )
            self.assertTrue((uploads.payload_dir / "job-1.json").is_file())

            result = uploads.cancel_session(SESSION_ID)
            self.assertEqual(result["cancelled"], ["job-1"])
            self.assertFalse((uploads.payload_dir / "job-1.json").is_file())
            self.assertEqual(uploads.counts()["cancelled"], 1)

    def test_a_cancelled_upload_is_never_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            uploads = self._service(data_dir)
            uploads.enqueue(
                kind="notion",
                study_id="s",
                participant_id="p",
                session_id=SESSION_ID,
                label="Notion",
                payload={"answers": {}},
                job_id="job-1",
            )
            uploads.cancel_session(SESSION_ID)

            self.assertEqual(uploads.process_due_jobs_once(), 0)
            with self.assertRaises(UploadJobError):
                uploads.retry(job_id="job-1")
            self.assertEqual(uploads.retry(all_failed=True)["retried"], 0)

    def test_a_cancellation_survives_a_restart(self) -> None:
        """The journal replay must not resurrect withdrawn work."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            uploads = self._service(data_dir)
            uploads.enqueue(
                kind="notion",
                study_id="s",
                participant_id="p",
                session_id=SESSION_ID,
                label="Notion",
                payload={"answers": {}},
                job_id="job-1",
            )
            uploads.cancel_session(SESSION_ID)

            restarted = self._service(data_dir)
            self.assertEqual(restarted.counts()["cancelled"], 1)
            self.assertEqual(restarted.process_due_jobs_once(), 0)

    def test_an_already_published_destination_is_named_not_claimed_deleted(self) -> None:
        """This process cannot reach someone else's server; it must not pretend."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            uploads = self._service(data_dir)
            uploads.register_executor("notion", lambda _payload: {"ok": True})
            uploads.enqueue(
                kind="notion",
                study_id="s",
                participant_id="p",
                session_id=SESSION_ID,
                label="Notion",
                payload={"answers": {}},
                job_id="job-1",
            )
            uploads.process_due_jobs_once()

            session = data_dir / "sessions" / SESSION_ID
            _populate_session(session)
            state = WithdrawalService(data_dir, upload_jobs=uploads).withdraw(
                session_id=SESSION_ID, session_root=session
            )

            published = state["already_published"]
            self.assertEqual([entry["job_id"] for entry in published], ["job-1"])
            marker = json.loads((session / WITHDRAWN_MARKER).read_text(encoding="utf-8"))
            self.assertEqual([entry["kind"] for entry in marker["already_published"]], ["notion"])


class WithdrawnSessionVisibilityTests(unittest.TestCase):
    """A withdrawn session must not simply vanish from the list."""

    def _tombstoned_tree(self, data_dir: Path) -> Path:
        session = data_dir / "study-a" / "participants" / "p1" / "sessions" / SESSION_ID
        _populate_session(session)
        WithdrawalService(data_dir).withdraw(session_id=SESSION_ID, session_root=session)
        return session

    def test_a_withdrawn_session_is_still_listed_as_withdrawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            self._tombstoned_tree(data_dir)
            sessions = list_sessions(data_dir)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["lifecycle"], WITHDRAWN)
            self.assertEqual(sessions[0]["session_id"], SESSION_ID)

    def test_the_listing_carries_no_answers_from_the_withdrawn_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            self._tombstoned_tree(data_dir)
            summary = list_sessions(data_dir)[0]
            self.assertEqual(summary["answers_count"], 0)
            self.assertEqual(summary["files"], [])


if __name__ == "__main__":
    unittest.main()
