from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# An absent worker is unavailable for a different reason on each platform: off Linux
# the bundled binary is simply missing, while Linux stays fail-closed by policy until
# a canonical XDF core passes its release gate. Either way the state must be explicit.
WORKER_UNAVAILABLE_REASON = (
    "fail-closed" if platform.system().strip().casefold() == "linux" else "not found"
)

from study_runner.recording.artifacts import ArtifactStore, SessionIdentity
from study_runner.recording.coordinator import RecordingCoordinator, SegmentLedger
from study_runner.recording.errors import CommandConflictError, WorkerProtocolError
from study_runner.recording.recovery import (
    DEFAULT_RECORDING_LEASE_SECONDS,
    RecordingLeaseStore,
)
from study_runner.recording.worker_protocol import (
    PersistentCommandLedger,
    LoopbackWorkerClient,
    WorkerCommand,
    WorkerCommandRouter,
    WorkerEndpointState,
)
from study_runner.recording.worker_binary import BundledWorkerLocator


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RecordingWorkerProtocolTests(unittest.TestCase):
    def test_command_id_is_durable_and_idempotent(self) -> None:
        calls: list[str] = []
        command = WorkerCommand("freeze_session", {"session_id": "s1"}, command_id="freeze-1")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commands.json"
            first_ledger = PersistentCommandLedger(path)
            first = first_ledger.execute(command, lambda item: calls.append(item.command_id) or {"closed": True})
            second_ledger = PersistentCommandLedger(path)
            second = second_ledger.execute(command, lambda item: calls.append("unexpected") or {})

            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertTrue(second.replayed)
            self.assertEqual(calls, ["freeze-1"])

            with self.assertRaises(CommandConflictError):
                second_ledger.execute(
                    WorkerCommand("freeze_session", {"session_id": "other"}, command_id="freeze-1"),
                    lambda item: {},
                )

    def test_worker_endpoint_rejects_non_loopback_host(self) -> None:
        with self.assertRaises(ValueError):
            WorkerEndpointState(session_id="s1", host="0.0.0.0", port=3010, token="x" * 32)

    def test_worker_router_binds_header_and_payload_to_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            router = WorkerCommandRouter(
                token="x" * 32,
                ledger=PersistentCommandLedger(Path(tmp) / "commands.json"),
                handlers={"health": lambda _command: {"healthy": True}},
                session_id="s1",
            )
            payload = WorkerCommand("health", {"session_id": "s1"}, command_id="health-1").as_dict()
            response = router.handle("Bearer " + "x" * 32, payload, session_id="s1")
            self.assertTrue(response.ok)

            with self.assertRaises(WorkerProtocolError):
                router.handle("Bearer " + "x" * 32, payload, session_id="other")
            with self.assertRaises(WorkerProtocolError):
                router.handle(
                    "Bearer " + "x" * 32,
                    WorkerCommand("health", {"session_id": "other"}, command_id="health-2").as_dict(),
                    session_id="s1",
                )

    def test_missing_bundled_worker_is_an_explicit_unavailable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = BundledWorkerLocator(Path(tmp)).locate()

        self.assertFalse(status.available)
        self.assertIsNone(status.path)
        self.assertIn(WORKER_UNAVAILABLE_REASON, status.reason)

    def test_worker_restart_allocates_new_segment_never_appends(self) -> None:
        identity = SessionIdentity(
            "study",
            "participant",
            "session-1",
            dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = ArtifactStore(Path(tmp)).reserve(identity)
            ledger = SegmentLedger(paths, "brainbit")
            first = ledger.allocate("worker-start-1", worker_generation=1)
            ledger.mark_recording(first.allocation_id)
            ledger.absolute_path(first).write_bytes(b"possibly truncated xdf")

            second = ledger.allocate("worker-start-2", worker_generation=2)
            replay = ledger.allocate("worker-start-2", worker_generation=2)
            records = ledger.records()

            self.assertEqual(first.filename, "part-0001.xdf")
            self.assertEqual(second.filename, "part-0002.xdf")
            self.assertEqual(replay, second)
            self.assertEqual(records[0].state, "interrupted")
            self.assertEqual(records[1].state, "allocated")
            self.assertFalse(ledger.absolute_path(second).exists())

    def test_recording_lease_expires_after_fifteen_minutes(self) -> None:
        clock = MutableClock(1000.0)
        with tempfile.TemporaryDirectory() as tmp:
            store = RecordingLeaseStore(Path(tmp) / "lease.json", clock=clock)
            lease = store.start("session-1", worker_generation=1)
            self.assertEqual(lease.lease_until_epoch, 1000.0 + DEFAULT_RECORDING_LEASE_SECONDS)

            clock.value = lease.lease_until_epoch - 0.1
            self.assertEqual(store.expire_if_due().state, "active")
            clock.value = lease.lease_until_epoch
            self.assertEqual(store.expire_if_due().state, "expired")

    def test_merge_operation_identity_is_stable_across_command_retries(self) -> None:
        identity = SessionIdentity(
            "study",
            "participant",
            "session-merge",
            dt.datetime(2026, 7, 31, tzinfo=dt.timezone.utc),
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = ArtifactStore(Path(tmp)).reserve(identity)
            source = paths.plugin_dir("brainbit") / "part-0001.xdf"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"stable source")
            requests: list[dict] = []
            endpoint = WorkerEndpointState.create(session_id=identity.session_id, port=32123)

            def transport(_endpoint, body, _headers, _timeout):
                payload = json.loads(body.decode("utf-8"))
                requests.append(payload)
                return {
                    "protocol_version": 1,
                    "command_id": payload["command_id"],
                    "ok": True,
                    "result": {},
                    "error": None,
                    "replayed": False,
                }

            coordinator = RecordingCoordinator(
                paths,
                LoopbackWorkerClient(endpoint, transport=transport),
            )
            coordinator.merge([source], paths.merged_xdf, command_id="merge-attempt-1")
            coordinator.merge([source], paths.merged_xdf, command_id="merge-attempt-2")

            first = requests[0]["payload"]
            second = requests[1]["payload"]
            self.assertEqual(first["operation_id"], second["operation_id"])
            self.assertEqual(first["temporary_output_path"], second["temporary_output_path"])
            self.assertEqual(first["source_artifacts"][0]["sha256"], second["source_artifacts"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
