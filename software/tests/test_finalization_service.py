from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.recording.artifacts import sha256_file
from study_runner.backend.services.delivery.artifact_manifest_service import ArtifactManifestStore
from study_runner.backend.services.studies.card_summary_service import CardSummaryBuilder
from study_runner.backend.services.delivery.destination_plugin_service import (
    DestinationPluginDefinition,
)
from study_runner.backend.services.delivery.finalization_service import (
    DeferredStep,
    FinalizationError,
    FinalizationService,
    InvalidTransitionError,
    StepResult,
    SubmissionConflictError,
)


SUBMISSION = {
    "submission_id": "submission-1",
    "session_id": "session-1",
    "study_id": "Study A",
    "participant_id": "p01",
    "timestamp_start": "2026-07-31T10:00:00Z",
    "timestamp_end": "2026-07-31T10:01:00Z",
    "answers": {"q1": 4},
    "card_events": [
        {
            "event_id": "card-event-1",
            "shown_event_id": "card-1-shown",
            "answered_event_id": "card-1-answered",
            "question_index": 1,
            "question_type": "slider",
            "client_start_trigger_epoch_ms": 1000,
            "client_stop_trigger_epoch_ms": 2000,
        }
    ],
    "study_end_event": {"event_id": "study-end-session-1"},
}


class OneStreamReader:
    def read_streams(self, _path):
        return [
            {
                "stream_key": "sensor.one",
                "plugin_key": "fixture",
                "nominal_rate_hz": 2,
                "timestamps": [1.0, 1.5, 2.0],
                "samples": [{"value": 2.0}, {"value": 4.0}, {"value": 99.0}],
            },
            {
                "stream_key": "lsl.markers",
                "plugin_key": "lsl",
                "nominal_rate_hz": 0,
                "timestamps": [1.0, 2.0, 2.1],
                "samples": [
                    {"marker": "event_id=card-1-shown|phase=shown"},
                    {"marker": "event_id=card-1-answered|phase=answered"},
                    {"marker": "event_id=study-end-session-1|phase=study_end"},
                ],
            },
        ]


class SuccessfulRecordingAdapter:
    def freeze(self, _context):
        return {"closed_segments": 1}

    def validate_sources(self, context):
        source = context.paths.plugin_dir("fixture") / "part-0001.xdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"source-xdf")
        return {"source_paths": [source.relative_to(context.paths.root).as_posix()]}

    def merge(self, context):
        context.paths.merged_xdf.parent.mkdir(parents=True, exist_ok=True)
        context.paths.merged_xdf.write_bytes(b"merged-xdf")
        return {"output": "derived/session.xdf"}

    def validate_merge(self, _context):
        return {"stream_parity": True, "native_rates_preserved": True}


class FailingRecordingAdapter(SuccessfulRecordingAdapter):
    def validate_sources(self, _context):
        raise FinalizationError("required source fixture is unreadable")


class RecordingDestinationHandler:
    def __init__(self, *, defer_notion_once: bool = False):
        self.calls = []
        self.defer_notion_once = defer_notion_once

    def publish(self, destination, context):
        self.calls.append(destination)
        if destination == "notion" and self.defer_notion_once:
            self.defer_notion_once = False
            raise DeferredStep("Notion is queued", retry_after_seconds=1)
        return StepResult("done", {"destination": destination, "session_id": context.state["session_id"]})


class FailedPersistentDestinationHandler(RecordingDestinationHandler):
    def __init__(self):
        super().__init__()
        self.retry_calls = []
        self.fail_notion = True

    def publish(self, destination, context):
        self.calls.append(destination)
        if destination == "notion" and self.fail_notion:
            raise FinalizationError("persistent notion upload failed")
        return StepResult("done", {"destination": destination, "session_id": context.state["session_id"]})

    def retry(self, destination, _context):
        self.retry_calls.append(destination)
        self.fail_notion = False


class DeferredNextcloudDestinationHandler(RecordingDestinationHandler):
    def publish(self, destination, context):
        self.calls.append(destination)
        if destination == "nextcloud":
            raise DeferredStep("Nextcloud attention backup is queued", retry_after_seconds=1)
        return StepResult("done", {"destination": destination, "session_id": context.state["session_id"]})


class MutableClock:
    def __init__(self, value=1000.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class FinalizationServiceTests(unittest.TestCase):
    def _service(self, root: Path, **kwargs):
        return FinalizationService(
            root,
            recording_adapter=kwargs.get("recording_adapter", SuccessfulRecordingAdapter()),
            destination_handler=kwargs.get("destination_handler"),
            destination_definitions=kwargs.get("destination_definitions"),
            card_summary_builder=CardSummaryBuilder(OneStreamReader()),
            clock=kwargs.get("clock", MutableClock()),
        )

    def test_commit_is_idempotent_and_processing_publishes_complete_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            created = service.commit_submission(SUBMISSION, config_data={"study_settings": {}}, recording_expected=True)
            repeated = service.commit_submission(SUBMISSION, config_data={"study_settings": {}}, recording_expected=True)

            self.assertTrue(created["created"])
            self.assertFalse(repeated["created"])
            self.assertEqual(created["job_id"], repeated["job_id"])
            session_root = root / created["session_path"]
            self.assertTrue((session_root / "submission.json").is_file())
            self.assertTrue((session_root / "finalization-state.json").is_file())
            self.assertEqual(service.get(created["job_id"])["status"], "queued")

            self.assertEqual(service.process_due_jobs_once(), 1)
            completed = service.get(created["job_id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["quality_status"], "valid")
            self.assertTrue((session_root / "derived" / "session.xdf").is_file())
            self.assertTrue((session_root / "card-summary.json").is_file())
            self.assertTrue((session_root / "result.json").is_file())
            self.assertTrue((session_root / "manifest.json").is_file())
            self.assertTrue((session_root / "checksums.sha256").is_file())
            self.assertTrue((session_root / "COMPLETE.json").is_file())
            self.assertFalse((session_root / "ATTENTION_REQUIRED.json").exists())

            restarted = self._service(root)
            self.assertEqual(restarted.get(created["job_id"])["status"], "completed")
            self.assertEqual(restarted.process_due_jobs_once(), 0)

    def test_durable_commit_survives_projection_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            from study_runner.backend.services.delivery import finalization_service as module

            real_atomic_write = module.atomic_write_json

            def fail_submission_projection(path, payload):
                if Path(path).name == "submission.json":
                    raise OSError("simulated projection failure")
                return real_atomic_write(path, payload)

            with mock.patch.object(module, "atomic_write_json", side_effect=fail_submission_projection):
                created = service.commit_submission(SUBMISSION, recording_expected=True)

            self.assertTrue(created["created"])
            session_root = root / created["session_path"]
            self.assertTrue((session_root / ".submission-commit.json").is_file())
            self.assertFalse((session_root / "submission.json").exists())
            self.assertEqual(service.get(created["job_id"])["status"], "queued")
            repeated = service.commit_submission(SUBMISSION, recording_expected=True)
            self.assertFalse(repeated["created"])

            restarted = self._service(root)
            self.assertTrue((session_root / "submission.json").is_file())
            self.assertEqual(restarted.get(created["job_id"])["status"], "queued")

    def test_v3_destination_selection_overrides_legacy_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._service(Path(temp_dir))
            job = service.commit_submission(
                SUBMISSION,
                config_data={
                    "study_settings": {
                        "notion_enabled": False,
                        "nextcloud_enabled": True,
                        "plugins": {
                            "notion": {"enabled": True, "required": False, "settings": {}},
                            "nextcloud": {"enabled": False, "required": False, "settings": {}},
                        },
                    }
                },
                recording_expected=True,
            )

            steps = {step["key"]: step for step in service.get(job["job_id"])["steps"]}
            self.assertEqual(steps["publish_notion"]["status"], "pending")
            self.assertEqual(steps["publish_nextcloud"]["status"], "skipped")

    def test_fixture_destination_adds_and_executes_a_step_without_core_key_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = RecordingDestinationHandler()
            definition = DestinationPluginDefinition(
                plugin_key="fixture_export",
                destination="fixture_export",
                label="Fixture export",
            )
            service = self._service(
                Path(temp_dir),
                destination_handler=destination,
                destination_definitions=(definition,),
            )
            job = service.commit_submission(
                SUBMISSION,
                config_data={
                    "study_settings": {
                        "plugins": {
                            "fixture_export": {
                                "enabled": True,
                                "required": False,
                                "settings": {"bucket": "fixture"},
                            }
                        }
                    }
                },
                recording_expected=True,
            )

            step_keys = [step["key"] for step in job["steps"]]
            self.assertIn("publish_fixture_export", step_keys)
            self.assertNotIn("publish_notion", step_keys)
            self.assertNotIn("publish_nextcloud", step_keys)
            service.process_due_jobs_once()
            self.assertEqual(destination.calls, ["fixture_export"])
            self.assertEqual(service.get(job["job_id"])["status"], "completed")

    def test_multiple_source_purge_destinations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            definitions = tuple(
                DestinationPluginDefinition(
                    plugin_key=key,
                    destination=key,
                    label=key,
                    purge_verified_sources=True,
                )
                for key in ("first_archive", "second_archive")
            )
            with self.assertRaisesRegex(ValueError, "Only one upload destination"):
                self._service(
                    Path(temp_dir),
                    destination_definitions=definitions,
                )

    def test_conflicting_reuse_of_submission_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._service(Path(temp_dir))
            service.commit_submission(SUBMISSION, recording_expected=True)
            with self.assertRaises(SubmissionConflictError):
                service.commit_submission({**SUBMISSION, "answers": {"q1": 5}}, recording_expected=True)

    def test_core_failure_requires_attention_and_admin_can_confirm_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = RecordingDestinationHandler()
            service = self._service(
                Path(temp_dir),
                recording_adapter=FailingRecordingAdapter(),
                destination_handler=destination,
            )
            job = service.commit_submission(
                SUBMISSION,
                config_data={"study_settings": {"notion_enabled": True, "nextcloud_enabled": True}},
                recording_expected=True,
            )
            service.process_due_jobs_once()
            failed = service.get(job["job_id"])
            self.assertEqual(failed["status"], "attention_required")
            self.assertEqual(next(step for step in failed["steps"] if step["key"] == "validate_sources")["status"], "failed")
            session_root = Path(temp_dir) / failed["session_path"]
            self.assertTrue((session_root / "ATTENTION_REQUIRED.json").is_file())

            service.process_due_jobs_once()
            self.assertEqual(destination.calls, ["nextcloud"], "Notion must remain blocked before confirmation")
            degraded = service.confirm_degraded(job["job_id"], reason="Sensor cable was removed", confirmed_by="operator-1")
            self.assertEqual(degraded["status"], "completed_degraded")
            self.assertEqual(degraded["degraded_confirmation"]["confirmed_by"], "operator-1")
            service.process_due_jobs_once()
            self.assertIn("notion", destination.calls)
            self.assertTrue((session_root / "COMPLETE.json").is_file())

    def test_degraded_confirmation_waits_for_attention_backup_to_settle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = DeferredNextcloudDestinationHandler()
            service = self._service(
                Path(temp_dir),
                recording_adapter=FailingRecordingAdapter(),
                destination_handler=destination,
            )
            job = service.commit_submission(
                SUBMISSION,
                config_data={"study_settings": {"nextcloud_enabled": True}},
                recording_expected=True,
            )

            service.process_due_jobs_once()
            with self.assertRaisesRegex(InvalidTransitionError, "Nextcloud backup"):
                service.confirm_degraded(job["job_id"], reason="Known sensor loss")

            service.process_due_jobs_once()
            with self.assertRaisesRegex(InvalidTransitionError, "Nextcloud backup"):
                service.confirm_degraded(job["job_id"], reason="Known sensor loss")

    def test_destinations_progress_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = MutableClock()
            destination = RecordingDestinationHandler(defer_notion_once=True)
            service = self._service(Path(temp_dir), destination_handler=destination, clock=clock)
            job = service.commit_submission(
                SUBMISSION,
                config_data={"study_settings": {"notion_enabled": True, "nextcloud_enabled": True}},
                recording_expected=True,
            )
            service.process_due_jobs_once()
            pending = service.get(job["job_id"])
            notion = next(step for step in pending["steps"] if step["key"] == "publish_notion")
            nextcloud = next(step for step in pending["steps"] if step["key"] == "publish_nextcloud")
            self.assertEqual(notion["status"], "retrying")
            self.assertEqual(nextcloud["status"], "done")
            self.assertEqual(
                service.process_due_jobs_once(),
                0,
                "a future retry must not hot-loop because purge is still pending",
            )

            retried = service.retry(job["job_id"], step_key="publish_notion")
            retried_nextcloud = next(step for step in retried["steps"] if step["key"] == "publish_nextcloud")
            self.assertEqual(retried_nextcloud["status"], "done")
            service.process_due_jobs_once()
            self.assertEqual(service.get(job["job_id"])["status"], "completed")
            self.assertEqual(destination.calls.count("nextcloud"), 1)

    def test_destination_retry_requeues_persistent_job_and_preserves_merge_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = FailedPersistentDestinationHandler()
            service = self._service(Path(temp_dir), destination_handler=destination)
            job = service.commit_submission(
                SUBMISSION,
                config_data={"study_settings": {"notion_enabled": True}},
                recording_expected=True,
            )
            service.process_due_jobs_once()
            state = service._jobs[job["job_id"]]
            self.assertEqual(next(step for step in state["steps"] if step["key"] == "publish_notion")["status"], "failed")
            self.assertTrue(state["runtime"]["merge_parity"])

            service.retry(job["job_id"], step_key="publish_notion")

            self.assertEqual(destination.retry_calls, ["notion"])
            self.assertTrue(state["runtime"]["merge_parity"])
            service.process_due_jobs_once()
            self.assertEqual(service.get(job["job_id"])["status"], "completed")

    def test_local_source_purge_requires_matching_remote_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = self._service(root)
            job = service.commit_submission(SUBMISSION, recording_expected=True)
            state = service._jobs[job["job_id"]]
            context = service._context(state)
            source = context.paths.plugin_dir("fixture") / "part-0001.xdf"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"native")
            context.paths.merged_xdf.parent.mkdir(parents=True, exist_ok=True)
            context.paths.merged_xdf.write_bytes(b"merged")
            manifest = ArtifactManifestStore()
            manifest.write(
                context.paths,
                identity=context.paths.identity,
                quality_status="valid",
                merge_parity=True,
            )
            relative = source.relative_to(context.paths.root).as_posix()
            result = manifest.purge_plugin_xdfs(
                context.paths,
                remote_sha256={relative: sha256_file(source)},
                session_status="completed",
                merge_parity=True,
            )
            self.assertEqual(result["removed"], [relative])
            self.assertFalse(source.exists())
            self.assertTrue(context.paths.merged_xdf.exists())
            persisted = json.loads((context.paths.root / "manifest.json").read_text(encoding="utf-8"))
            source_entry = next(item for item in persisted["artifacts"] if item["path"] == relative)
            self.assertFalse(source_entry["local_present"])
            self.assertTrue(source_entry["remote_verified"])

    def test_source_purge_reconciles_crash_after_unlink_before_progress_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._service(Path(temp_dir))
            job = service.commit_submission(SUBMISSION, recording_expected=True)
            context = service._context(service._jobs[job["job_id"]])
            source = context.paths.plugin_dir("fixture") / "part-0001.xdf"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"native")
            context.paths.merged_xdf.parent.mkdir(parents=True, exist_ok=True)
            context.paths.merged_xdf.write_bytes(b"merged")
            store = ArtifactManifestStore()
            manifest = store.write(
                context.paths,
                identity=context.paths.identity,
                quality_status="valid",
                merge_parity=True,
            )
            relative = source.relative_to(context.paths.root).as_posix()
            digest = sha256_file(source)
            manifest["source_purge"] = {
                "status": "prepared",
                "planned": [{"path": relative, "sha256": digest}],
                "removed": [],
                "remote": "nextcloud",
            }
            (context.paths.root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            source.unlink()  # exact crash window being replayed

            result = store.purge_plugin_xdfs(
                context.paths,
                remote_sha256={relative: digest},
                session_status="completed",
                merge_parity=True,
            )

            self.assertEqual(result["removed"], [relative])
            reconciled = json.loads(
                (context.paths.root / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(reconciled["source_purge"]["status"], "completed")
            entry = next(item for item in reconciled["artifacts"] if item["path"] == relative)
            self.assertFalse(entry["local_present"])


if __name__ == "__main__":
    unittest.main()
