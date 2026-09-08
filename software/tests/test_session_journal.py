"""Recovery must follow durable append order across clock changes/restarts."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from study_runner.backend.services.studies import session_journal_service as journal


class SessionJournalOrderingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = journal.SessionJournalStore(self.root)

    def append(self, value, store=None):
        return (store or self.store).append("session", "s1", "changed", {"value": value})

    def test_clock_steps_and_restart_keep_latest_durable_snapshot(self):
        with patch.object(journal.time, "time_ns", side_effect=[200, 100, 500, 50]):
            self.append("first")
            self.append("second")
            restarted = journal.SessionJournalStore(self.root)
            self.append("third", restarted)
            self.append("fourth", restarted)
        self.assertEqual(restarted.latest_snapshots("session")["s1"]["snapshot"], {"value": "fourth"})
        self.assertEqual([r["sequence"] for r in restarted.records("session", "s1")], [1, 2, 3, 4])

    def test_legacy_records_replay_in_append_order_without_rewriting_bytes(self):
        records = [self.append("first"), self.append("second")]
        for record, timestamp in zip(records, [200, 100]):
            record.pop("sequence")
            record["order_ns"] = timestamp
        path = self.store.journal_path("session", "s1")
        original = b"".join(journal._canonical_json(r) + b"\n" for r in records)
        path.write_bytes(original)
        restarted = journal.SessionJournalStore(self.root)
        self.assertEqual(restarted.latest_snapshots("session")["s1"]["snapshot"], {"value": "second"})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.append("third", restarted)["sequence"], 3)
        self.assertTrue(path.read_bytes().startswith(original))

    def test_torn_tail_is_removed_before_append_after_restart(self):
        self.append("first")
        path = self.store.journal_path("session", "s1")
        confirmed = path.read_bytes()
        with path.open("ab") as handle:
            handle.write(b'{"schema":"study-runner/session-journal/v1","snapshot":')
        restarted = journal.SessionJournalStore(self.root)
        self.assertEqual(self.append("second", restarted)["sequence"], 2)
        self.assertTrue(path.read_bytes().startswith(confirmed))
        self.assertEqual(len(restarted.records("session", "s1")), 2)

    def test_complete_legacy_tail_without_newline_stays_readable_on_append(self):
        self.append("first")
        path = self.store.journal_path("session", "s1")
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        restarted = journal.SessionJournalStore(self.root)
        self.append("second", restarted)
        self.assertEqual(len(restarted.records("session", "s1")), 2)

    def test_corrupt_interior_fails_without_modifying_file(self):
        self.append("first")
        path = self.store.journal_path("session", "s1")
        original = path.read_bytes() + b'{invalid}\n'
        path.write_bytes(original)
        with self.assertRaises(journal.SessionJournalCorruptionError):
            self.append("second", journal.SessionJournalStore(self.root))
        self.assertEqual(path.read_bytes(), original)

    def test_sequence_tampering_is_rejected(self):
        self.append("first")
        record = self.append("second")
        record["sequence"] = 1
        path = self.store.journal_path("session", "s1")
        path.write_bytes(path.read_bytes().splitlines(keepends=True)[0] + journal._canonical_json(record) + b"\n")
        with self.assertRaises(journal.SessionJournalCorruptionError):
            self.store.records("session", "s1")

    def test_store_instances_serialize_sequence_allocation(self):
        stores = [journal.SessionJournalStore(self.root) for _ in range(3)]
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda n: self.append(n, stores[n % 3]), range(18)))
        records = self.store.records("session", "s1")
        self.assertEqual([r["sequence"] for r in records], list(range(1, 19)))
        self.assertEqual({r["snapshot"]["value"] for r in records}, set(range(18)))

    def test_append_failure_does_not_acknowledge_or_cache_new_position(self):
        self.append("first")
        with patch.object(journal, "_append_fsynced", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.append("failed")
        self.assertEqual(self.append("second")["sequence"], 2)

    def test_existing_legacy_archive_survives_order_fix(self):
        records = [self.append("first"), self.append("second")]
        for record, timestamp in zip(records, [200, 100]):
            record.pop("sequence")
            record["order_ns"] = timestamp
        self.store.journal_path("session", "s1").write_bytes(
            b"".join(journal._canonical_json(r) + b"\n" for r in records)
        )
        streams = {"session": sorted(records, key=journal._record_order), "trial": []}
        digest = hashlib.sha256()
        for stream, values in streams.items():
            digest.update(stream.encode("ascii") + b"\0")
            digest.update(journal._canonical_json(values))
        archive = self.root / "result" / "logs" / "session-journals.archive.json"
        archive.parent.mkdir(parents=True)
        original = json.dumps({"schema": journal.ARCHIVE_SCHEMA, "session_id": "s1", "source_sha256": digest.hexdigest(), "streams": {}}).encode()
        archive.write_bytes(original)
        result = self.store.archive_session("s1", archive.parent.parent, finalization_status="completed")
        self.assertTrue(result["already_archived"])
        self.assertEqual(archive.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
