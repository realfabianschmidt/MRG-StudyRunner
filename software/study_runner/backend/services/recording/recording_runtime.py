"""Flask-facing orchestration for the native, session-scoped XDF worker.

This module deliberately contains no XDF encoding.  It reserves the canonical
session tree, starts the app-owned native worker, submits idempotent recording
commands, and exposes a finalization adapter which validates worker artifacts
with :mod:`pyxdf`.

The repository does not silently substitute another file format when the
native worker is absent.  A study which selected a required recording source
is therefore blocked by readiness until the bundled XDFWriter worker exists.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from study_runner.plugin_framework.registry import get_backup_projection_specs
from study_runner.recording_worker.lsl_recording import lsl_version_info, require_pylsl

from study_runner.recording.artifacts import ArtifactPaths, ArtifactStore, SessionIdentity
from study_runner.recording.backup import BackupSampler, projections_from_manifest
from study_runner.recording.coordinator import RecordingCoordinator, SegmentLedger
from study_runner.recording.errors import WorkerUnavailableError
from study_runner.recording.recovery import RecordingLeaseStore
from study_runner.recording.worker_binary import BundledWorkerLocator, WorkerBinaryAvailability
from study_runner.recording.worker_protocol import (
    LoopbackWorkerClient,
    WorkerEndpointState,
    WorkerStateStore,
)
from study_runner.recording.xdf import (
    NativeWorkerXdfBackend,
    PyXdfInspector,
    XdfArtifactInspection,
    XdfValidationReport,
    validate_merge_parity,
    validate_sources,
    validator_dependency_status,
)
from study_runner.shared.atomic_io import atomic_write_json
from .recording_dependencies import (
    INTERNAL_RECORDING_SOURCE_KEYS,
    get_plugin_manifests_with_internal_sources,
    probe_lsl_dependencies,
    required_recording_plugins,
    selected_recording_plugins,
)
from .recording_finalization_adapter import RuntimeRecordingFinalizationAdapter
from .recording_quality import (
    backup_source_checks as _backup_source_checks,
    recording_lease_quality_checks as _recording_lease_quality_checks,
    scientific_source_checks as _scientific_source_checks,
)
from .recording_runtime_support import (
    RECORDING_COMMAND_TIMEOUT_SECONDS,
    RECORDING_PLAN_SCHEMA,
    RecordingRuntimeError,
    identity_from_session as _identity_from_session,
    parse_utc as _parse_utc,
    public_plan as _public_plan,
    read_object as _read_object,
    recovery_backup_grid_anchor as _recovery_backup_grid_anchor,
    require_worker_ok as _require_worker_ok,
    session_relative_path as _session_relative_path,
    wait_for_required_worker_sources as _wait_for_required_worker_sources,
)
from .recording_worker_launcher import (
    DetachedWorkerLauncher,
    WorkerLaunchSpec,
)


def recording_lsl_dependency_status() -> dict[str, Any]:
    """Probe Python and native liblsl before a study is allowed to start."""

    return probe_lsl_dependencies(require_pylsl, lsl_version_info)


class NativeWorkerLauncher(DetachedWorkerLauncher):
    """Compatibility facade retaining the public launcher and patch seam."""

    def __init__(self, worker: WorkerLaunchSpec | Path, **kwargs: Any) -> None:
        kwargs.setdefault(
            "client_factory",
            lambda *args, **client_kwargs: LoopbackWorkerClient(*args, **client_kwargs),
        )
        super().__init__(worker, **kwargs)


class RecordingRuntimeService:
    """Own one native recording session and reopen it from disk after restart."""

    def __init__(
        self,
        data_dir: Path,
        resource_root: Path,
        *,
        configured_worker_path: Path | None = None,
        launcher_factory: Callable[[WorkerLaunchSpec], NativeWorkerLauncher] = NativeWorkerLauncher,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.resource_root = Path(resource_root).resolve()
        self.artifacts = ArtifactStore(self.data_dir)
        self.locator = BundledWorkerLocator(
            self.resource_root,
            configured_path=configured_worker_path,
        )
        self._launcher_factory = launcher_factory
        self._allow_legacy_test_worker = (
            configured_worker_path is not None and launcher_factory is not NativeWorkerLauncher
        )
        self._clock = clock
        self._lock = threading.RLock()
        self._active_paths: dict[str, Path] = {}

    def availability(self) -> dict[str, Any]:
        binary = self.locator.locate()
        dependencies = validator_dependency_status()
        lsl_dependencies = recording_lsl_dependency_status()
        available = (
            binary.available
            and binary.canonical_xdf
            and binary.supports_merge
            and dependencies.ok
            and bool(lsl_dependencies["ok"])
        )
        reasons = [
            reason
            for reason in (binary.reason, dependencies.reason, lsl_dependencies.get("reason"))
            if reason
        ]
        return {
            "available": available,
            "canonical_xdf": bool(binary.canonical_xdf),
            "supports_merge": bool(binary.supports_merge),
            "worker_kind": binary.kind,
            "core": str(binary.core_path) if binary.core_path else None,
            "binary": str(binary.path) if binary.path else None,
            "protocol_version": binary.protocol_version,
            "validator": {
                "ok": dependencies.ok,
                "installed": dict(dependencies.installed),
                "expected": dict(dependencies.expected),
            },
            "lsl": lsl_dependencies,
            "reason": "; ".join(reasons) or None,
        }

    def preflight(
        self,
        config_data: Mapping[str, Any],
        hardware_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected = selected_recording_plugins(config_data)
        required = required_recording_plugins(config_data)
        availability = self.availability()
        reasons = [str(availability.get("reason") or "")] if not availability["available"] else []
        ready = not selected or bool(availability["available"])
        return {
            **availability,
            "recording_expected": bool(selected),
            "selected_plugins": list(selected),
            "required_plugins": list(required),
            "marker_stream_enabled": True,
            # Both are structural now -- exactly one Python module provides each,
            # so there is no "found zero" or "found two" left to report.
            "marker_plugin_ready": True,
            "clock_diagnostics_plugin_ready": True,
            "internal_recording_plugins": list(INTERNAL_RECORDING_SOURCE_KEYS),
            "disabled_lsl_bridges": [],
            "ready": ready,
            "reason": "; ".join(reason for reason in reasons if reason) or None,
        }

    def start_session(
        self,
        session: Mapping[str, Any],
        config_data: Mapping[str, Any],
        hardware_config: Mapping[str, Any],
    ) -> dict[str, Any]:
        selected = list(selected_recording_plugins(config_data))
        required = list(required_recording_plugins(config_data))
        if not selected:
            return {"recording_expected": False, "status": "skipped", "plugins": []}

        availability = self.locator.locate()
        dependencies = validator_dependency_status()
        lsl_dependencies = recording_lsl_dependency_status()
        if not self._availability_can_start(availability):
            raise RecordingRuntimeError(availability.reason or "native XDF worker is unavailable")
        if not dependencies.ok:
            raise RecordingRuntimeError(f"XDF validator dependency mismatch: {dependencies.reason}")
        if not lsl_dependencies["ok"]:
            raise RecordingRuntimeError(str(lsl_dependencies["reason"]))

        for source_key in INTERNAL_RECORDING_SOURCE_KEYS:
            selected.append(source_key)
            required.append(source_key)
        selected = list(dict.fromkeys(selected))
        required = list(dict.fromkeys(required))

        identity = _identity_from_session(session)
        paths = self.artifacts.reserve(identity)
        plan_path = paths.root / "recording-plan.json"
        with self._lock:
            if plan_path.is_file():
                plan = _read_object(plan_path)
                if plan.get("schema") != RECORDING_PLAN_SCHEMA:
                    raise RecordingRuntimeError("recording plan has an unsupported schema")
                self._active_paths[identity.session_id] = paths.root
                if plan.get("status") == "frozen":
                    return _public_plan(plan, reused=True)
                return self._reattach_or_recover(
                    paths,
                    plan,
                    availability,
                )

            plan: dict[str, Any] = {
                "schema": RECORDING_PLAN_SCHEMA,
                "session_id": identity.session_id,
                "study_id": identity.study_id,
                "participant_id": identity.participant_id,
                "status": "starting",
                "started_at_epoch": self._clock(),
                "recording_plugins": selected,
                "required_source_keys": required,
                "backup": None,
                "worker": None,
                "last_error": None,
            }
            atomic_write_json(plan_path, plan)
            try:
                self._start_worker_generation(
                    paths,
                    plan,
                    availability,
                    generation=1,
                )
                self._active_paths[identity.session_id] = paths.root
            except Exception as error:
                plan.update(status="attention_required", last_error=str(error), failed_at_epoch=self._clock())
                atomic_write_json(plan_path, plan)
                raise RecordingRuntimeError(str(error)) from error
        return _public_plan(plan)

    def _reattach_or_recover(
        self,
        paths: ArtifactPaths,
        plan: dict[str, Any],
        availability: WorkerBinaryAvailability,
    ) -> dict[str, Any]:
        """Reconcile an existing plan before a reused session may continue."""

        endpoint = WorkerStateStore(paths.worker_state_file).load()
        if endpoint is not None:
            try:
                client = self._healthy_client(paths, endpoint)
                health_response = client.send(
                    "health",
                    {
                        "session_id": paths.identity.session_id,
                        "generation": endpoint.generation,
                    },
                    command_id=(
                        f"health-reconcile-{paths.identity.session_id}-g{endpoint.generation}-"
                        f"{time.monotonic_ns()}"
                    ),
                )
                _require_worker_ok(
                    health_response.ok,
                    health_response.error,
                    "reconcile recording worker health",
                )
                if bool(health_response.result.get("frozen")):
                    lease_store = RecordingLeaseStore(paths.recording_lease_file)
                    lease = lease_store.expire_if_due()
                    if lease is not None and lease.state == "active":
                        lease = lease_store.mark_closed(reason="worker_reported_frozen")
                    freeze_reason = str(
                        health_response.result.get("freeze_reason")
                        or (lease.close_reason if lease is not None else "unknown")
                    )
                    plan.update(
                        status="attention_required",
                        worker=endpoint.public_dict(),
                        worker_reported_frozen=True,
                        worker_freeze_reason=freeze_reason,
                        frozen_at_epoch=self._clock(),
                        lease_state=lease.as_dict() if lease is not None else None,
                        last_error=(
                            "recording worker was already frozen before web-server reattachment: "
                            f"{freeze_reason}"
                        ),
                    )
                    atomic_write_json(paths.root / "recording-plan.json", plan)
                    return _public_plan(plan, reused=True)
                if plan.get("status") == "recording":
                    lease_store = RecordingLeaseStore(paths.recording_lease_file)
                    lease = lease_store.load()
                    if lease is None or lease.state != "active":
                        raise RecordingRuntimeError(
                            "recording worker already closed after its web-server lease expired"
                        )
                    lease_store.refresh(
                        paths.identity.session_id,
                        worker_generation=endpoint.generation,
                    )
                    plan.update(
                        worker=endpoint.public_dict(),
                        reattached_at_epoch=self._clock(),
                        worker_health_failures=0,
                        last_error=None,
                    )
                    atomic_write_json(paths.root / "recording-plan.json", plan)
                    return _public_plan(plan, reused=True)

                # Flask may have died after launching the worker but before all
                # start commands were acknowledged. Reissuing the same
                # generation command ids lets the worker ledger reconcile the
                # partial start without opening duplicate files.
                self._start_worker_generation(
                    paths,
                    plan,
                    availability,
                    generation=endpoint.generation,
                    endpoint=endpoint,
                    client=client,
                )
                return _public_plan(plan, reused=True)
            except Exception as error:
                plan["last_reconcile_error"] = str(error)

        previous_generation = endpoint.generation if endpoint is not None else int(
            ((plan.get("worker") or {}).get("generation") or 0)
        )
        generation = max(1, previous_generation + 1)
        plan.update(
            status="recovering",
            recovery_started_at_epoch=self._clock(),
            last_error=plan.get("last_reconcile_error") or plan.get("last_error"),
        )
        atomic_write_json(paths.root / "recording-plan.json", plan)
        try:
            self._start_worker_generation(
                paths,
                plan,
                availability,
                generation=generation,
            )
        except Exception as error:
            plan.update(
                status="attention_required",
                last_error=str(error),
                failed_at_epoch=self._clock(),
            )
            atomic_write_json(paths.root / "recording-plan.json", plan)
            raise RecordingRuntimeError(
                f"recording worker recovery failed: {error}"
            ) from error
        return _public_plan(plan, reused=True)

    def _healthy_client(
        self,
        paths: ArtifactPaths,
        endpoint: WorkerEndpointState,
    ) -> LoopbackWorkerClient:
        last_error = "worker health check failed"
        for attempt in range(3):
            client = LoopbackWorkerClient(
                endpoint,
                timeout_seconds=RECORDING_COMMAND_TIMEOUT_SECONDS,
            )
            try:
                response = client.send(
                    "health",
                    {"session_id": endpoint.session_id},
                    command_id=(
                        f"health-reattach-{endpoint.session_id}-g{endpoint.generation}-"
                        f"{time.monotonic_ns()}-{attempt}"
                    ),
                )
                if response.ok:
                    WorkerStateStore(paths.worker_state_file).touch()
                    return client
                last_error = response.error or last_error
            except Exception as error:
                last_error = str(error)
        raise WorkerUnavailableError(last_error)

    def _start_worker_generation(
        self,
        paths: ArtifactPaths,
        plan: dict[str, Any],
        availability: WorkerBinaryAvailability,
        *,
        generation: int,
        endpoint: WorkerEndpointState | None = None,
        client: LoopbackWorkerClient | None = None,
    ) -> None:
        """Start/reconcile one generation and allocate append-never segments."""

        if endpoint is None or client is None:
            launcher = self._launcher_factory(
                WorkerLaunchSpec(availability=availability, resource_root=self.resource_root)
            )
            endpoint, client = launcher.launch(paths, generation=generation)
        if endpoint.generation != generation:
            raise RecordingRuntimeError("recording worker generation mismatch")

        manifests = get_plugin_manifests_with_internal_sources()
        recording_plugins = [str(key) for key in plan.get("recording_plugins") or []]
        required_sources = {str(key) for key in plan.get("required_source_keys") or []}
        optional_source_warnings: list[dict[str, Any]] = []
        backend = NativeWorkerXdfBackend(RecordingCoordinator(paths, client))
        for plugin_key in recording_plugins:
            manifest = manifests.get(plugin_key) or {}
            capabilities = set(manifest.get("capabilities") or [])
            required_source = plugin_key in required_sources
            response = backend.start_source(
                plugin_key,
                manifest.get("streams") or [],
                command_id=(
                    f"start-source-{paths.identity.session_id}-{plugin_key}-g{generation}"
                ),
                require_stream_headers=required_source,
                require_fresh_primary_sample=(
                    required_source and "study_sensor" in capabilities
                ),
            )
            _require_worker_ok(response.ok, response.error, f"start source {plugin_key}")
            if not required_source:
                stream_states = response.result.get("streams") if isinstance(response.result, Mapping) else None
                if isinstance(stream_states, list):
                    unresolved = [
                        str(item.get("key") or "unknown")
                        for item in stream_states
                        if isinstance(item, Mapping) and not bool(item.get("header_written"))
                    ]
                    if unresolved:
                        optional_source_warnings.append(
                            {"plugin_key": plugin_key, "unresolved_streams": unresolved}
                        )

        projection_specs = get_backup_projection_specs(set(recording_plugins))
        if not projection_specs:
            raise RecordingRuntimeError(
                "active recording sensors do not provide the mandatory backup projection"
            )
        projection_objects = []
        for spec in projection_specs:
            projection_objects.extend(projections_from_manifest(spec["plugin_key"], spec))
        rate_hz = min(item.rate_hz for item in projection_objects)
        backup_channel_names = list(
            BackupSampler(tuple(projection_objects), start_monotonic=0.0).channel_names
        )
        backup = plan.get("backup") if isinstance(plan.get("backup"), dict) else {}
        segments = list(backup.get("segments") or [])
        if not segments and backup.get("relative_path"):
            segments.append(
                {
                    "generation": int(((plan.get("worker") or {}).get("generation") or 1)),
                    "relative_path": str(backup["relative_path"]),
                }
            )
        generation_segment = next(
            (
                segment
                for segment in segments
                if isinstance(segment, Mapping) and int(segment.get("generation") or 0) == generation
            ),
            None,
        )
        if generation_segment is not None:
            backup_path = _session_relative_path(paths, str(generation_segment["relative_path"]))
            grid_anchor_epoch = float(
                generation_segment.get("grid_anchor_epoch")
                or (
                    plan["started_at_epoch"]
                    if generation == 1
                    else _recovery_backup_grid_anchor(
                        float(plan["started_at_epoch"]),
                        rate_hz,
                        self._clock(),
                    )
                )
            )
            generation_segment["grid_anchor_epoch"] = grid_anchor_epoch
        else:
            canonical = paths.backup_xdf(rate_hz)
            backup_path = (
                canonical
                if not segments
                else canonical.with_name(
                    f"{canonical.stem}__recovery-{len(segments) + 1:04d}.xdf"
                )
            )
            grid_anchor_epoch = (
                float(plan["started_at_epoch"])
                if not segments
                else _recovery_backup_grid_anchor(
                    float(plan["started_at_epoch"]),
                    rate_hz,
                    self._clock(),
                )
            )
            generation_segment = {
                "generation": generation,
                "relative_path": backup_path.relative_to(paths.root).as_posix(),
                "grid_anchor_epoch": grid_anchor_epoch,
            }
            segments.append(generation_segment)

        source_rates = {
            f"{plugin_key}.{stream.get('key')}": stream.get("nominal_rate_hz")
            for plugin_key in recording_plugins
            for stream in (manifests.get(plugin_key) or {}).get("streams", [])
        }
        plan["backup"] = {
            "rate_hz": rate_hz,
            "grid_anchor_epoch": float(segments[0].get("grid_anchor_epoch") or plan["started_at_epoch"]),
            "relative_path": str(segments[0]["relative_path"]),
            "segments": segments,
            "artifact_role": "derived_backup",
            "resampling_strategy": "latest_cached_at_slowest_projection_grid; stale_to_nan",
            "quality_channels": ["valid", "sample_age_ms", "sequence", "status"],
            "channel_names": backup_channel_names,
            "source_rates_hz": source_rates,
            "active_plugins": recording_plugins,
            "projections": projection_specs,
        }
        atomic_write_json(paths.root / "recording-plan.json", plan)
        response = client.send(
            "start_backup_projection",
            {
                "session_id": paths.identity.session_id,
                "target_path": str(backup_path),
                "generation": generation,
                "rate_hz": rate_hz,
                "grid_anchor_epoch": grid_anchor_epoch,
                "artifact_role": "derived_backup",
                "resampling_strategy": "latest_cached_at_slowest_projection_grid; stale_to_nan",
                "active_plugins": recording_plugins,
                "source_rates_hz": source_rates,
                "quality_channels": ["valid", "sample_age_ms", "sequence", "status"],
                "channel_names": backup_channel_names,
                "projections": projection_specs,
            },
            command_id=f"start-backup-{paths.identity.session_id}-g{generation}",
        )
        _require_worker_ok(response.ok, response.error, "start backup projection")

        _wait_for_required_worker_sources(
            client,
            session_id=paths.identity.session_id,
            generation=generation,
            manifests=manifests,
            required_sources=required_sources,
        )

        lease_store = RecordingLeaseStore(paths.recording_lease_file)
        existing_lease = lease_store.load()
        if existing_lease is None:
            lease_store.start(paths.identity.session_id, worker_generation=generation)
        elif existing_lease.worker_generation == generation:
            if existing_lease.state != "active":
                raise RecordingRuntimeError(
                    f"recording lease is already {existing_lease.state}"
                )
            lease_store.refresh(paths.identity.session_id, worker_generation=generation)
        else:
            lease_store.restart_generation(
                paths.identity.session_id,
                worker_generation=generation,
            )

        plan.update(
            status="recording",
            worker=endpoint.public_dict(),
            ready_at_epoch=self._clock(),
            worker_health_failures=0,
            recovery_count=max(0, generation - 1),
            optional_source_warnings=optional_source_warnings,
            last_error=None,
        )
        atomic_write_json(paths.root / "recording-plan.json", plan)

    def refresh_lease(self, session_id: str) -> dict[str, Any] | None:
        paths = self._find_paths(session_id)
        if paths is None or not paths.recording_lease_file.is_file():
            return None
        with self._lock:
            endpoint = WorkerStateStore(paths.worker_state_file).load()
            if endpoint is None:
                return None
            lease_store = RecordingLeaseStore(paths.recording_lease_file)
            lease = lease_store.refresh(
                session_id,
                worker_generation=endpoint.generation,
            )
            plan = self._load_plan(paths)
            try:
                worker_client = LoopbackWorkerClient(
                    endpoint,
                    timeout_seconds=RECORDING_COMMAND_TIMEOUT_SECONDS,
                )
                response = worker_client.send(
                    "refresh_lease",
                    {"session_id": session_id, "lease_until_epoch": lease.lease_until_epoch},
                    command_id=(
                        f"lease-{session_id}-g{endpoint.generation}-"
                        f"{time.monotonic_ns()}"
                    ),
                )
                _require_worker_ok(response.ok, response.error, "refresh lease")
                health_response = worker_client.send(
                    "health",
                    {"session_id": session_id, "generation": endpoint.generation},
                    command_id=(
                        f"health-heartbeat-{session_id}-g{endpoint.generation}-"
                        f"{time.monotonic_ns()}"
                    ),
                )
                _require_worker_ok(
                    health_response.ok,
                    health_response.error,
                    "poll recording worker health",
                )
            except Exception as error:
                failures = int(plan.get("worker_health_failures") or 0) + 1
                plan.update(
                    worker_health_failures=failures,
                    last_worker_health_error=str(error),
                    last_worker_health_failure_at_epoch=self._clock(),
                )
                atomic_write_json(paths.root / "recording-plan.json", plan)
                if failures < 3:
                    return {
                        **lease.as_dict(),
                        "worker_recovery_pending": True,
                        "worker_health_failures": failures,
                    }
                availability = self.locator.locate()
                if not self._availability_can_start(availability):
                    raise RecordingRuntimeError(
                        availability.reason or "native XDF worker is unavailable for recovery"
                    ) from error
                recovered = self._reattach_or_recover(paths, plan, availability)
                new_lease = RecordingLeaseStore(paths.recording_lease_file).load()
                return {
                    **(new_lease.as_dict() if new_lease is not None else lease.as_dict()),
                    "worker_recovered": True,
                    "recording": recovered,
                }

            plan.update(
                worker_health_failures=0,
                last_worker_health_error=None,
                recording_worker_health=dict(health_response.result),
                recording_worker_health_at_epoch=self._clock(),
            )
            atomic_write_json(paths.root / "recording-plan.json", plan)
            return {**lease.as_dict(), "worker_health": dict(health_response.result)}

    def current_status(self) -> dict[str, Any] | None:
        """Return cached worker health without blocking the admin status route."""

        with self._lock:
            for session_id, root in reversed(tuple(self._active_paths.items())):
                try:
                    plan = _read_object(Path(root) / "recording-plan.json")
                except RecordingRuntimeError:
                    continue
                if plan.get("status") not in {
                    "starting",
                    "recording",
                    "recovering",
                    "attention_required",
                    "frozen",
                }:
                    continue
                return {
                    "session_id": session_id,
                    "status": plan.get("status"),
                    "worker": plan.get("worker"),
                    "health": plan.get("recording_worker_health"),
                    "health_at_epoch": plan.get("recording_worker_health_at_epoch"),
                    "worker_health_failures": int(plan.get("worker_health_failures") or 0),
                    "last_error": plan.get("last_worker_health_error") or plan.get("last_error"),
                }
        return None

    def freeze_worker(self, paths: ArtifactPaths, *, command_id: str) -> dict[str, Any]:
        plan = self._load_plan(paths)
        if plan.get("status") == "frozen":
            return {"already_frozen": True}
        lease_path = paths.recording_lease_file
        lease_store = RecordingLeaseStore(lease_path)
        lease = lease_store.load() if lease_path.is_file() else None
        if lease is not None and lease.state in {"closed", "expired"}:
            plan.update(
                status="frozen",
                frozen_at_epoch=self._clock(),
                freeze_mode="lease_already_closed",
            )
            atomic_write_json(paths.root / "recording-plan.json", plan)
            return {"already_closed": True, "lease_state": lease.state}
        try:
            backend = self._backend_for(paths)
            response = backend.freeze(command_id=command_id)
            _require_worker_ok(response.ok, response.error, "freeze recording")
            details = dict(response.result)
            freeze_mode = "ordered_worker_freeze"
        except WorkerUnavailableError as error:
            # Preserve and validate every fragment the dead worker left. An
            # unreadable required fragment will move the scientific state to
            # attention_required in the next step; inability to send a footer
            # alone must not prevent that evidence-based validation.
            details = {"worker_unavailable": True, "error": str(error)}
            freeze_mode = "ungraceful_worker_loss"
        if lease is not None:
            lease_store.mark_closed(reason="submission_finalization")
        plan.update(
            status="frozen",
            frozen_at_epoch=self._clock(),
            freeze_mode=freeze_mode,
        )
        atomic_write_json(paths.root / "recording-plan.json", plan)
        return details

    def source_artifacts(self, paths: ArtifactPaths) -> list[tuple[str, Path]]:
        plan = self._load_plan(paths)
        artifacts: list[tuple[str, Path]] = []
        for plugin_key in plan.get("recording_plugins") or []:
            ledger = SegmentLedger(paths, str(plugin_key))
            for record in ledger.records():
                candidate = ledger.absolute_path(record)
                # Declared segments are evidence, including when a crash or
                # external deletion made the path disappear. The inspector
                # must see every ledger entry so a missing middle part cannot
                # be hidden by readable earlier/later parts.
                artifacts.append((str(plugin_key), candidate))
        backup = plan.get("backup")
        if isinstance(backup, Mapping):
            raw_segments = backup.get("segments")
            relative_paths: list[str] = []
            if isinstance(raw_segments, list):
                for index, segment in enumerate(raw_segments, start=1):
                    if not isinstance(segment, Mapping) or not str(
                        segment.get("relative_path") or ""
                    ).strip():
                        raise RecordingRuntimeError(
                            f"backup segment {index} has no valid relative_path"
                        )
                    relative_paths.append(str(segment["relative_path"]))
            if not relative_paths and backup.get("relative_path"):
                relative_paths = [str(backup["relative_path"])]
            for relative_path in dict.fromkeys(path for path in relative_paths if path):
                candidate = _session_relative_path(paths, relative_path)
                artifacts.append(("derived_backup", candidate))
        return artifacts

    def inspect_sources(self, paths: ArtifactPaths) -> tuple[list[XdfArtifactInspection], Any]:
        plan = self._load_plan(paths)
        inspector = PyXdfInspector()
        inspections = [
            inspector.inspect(path, source_key=source_key)
            for source_key, path in self.source_artifacts(paths)
        ]
        required = list(plan.get("required_source_keys") or [])
        if isinstance(plan.get("backup"), Mapping):
            required.append("derived_backup")
        report = validate_sources(inspections, required_source_keys=required)
        scientific_issues, scientific_metrics = _scientific_source_checks(plan, inspections)
        lease_issues, lease_metrics = _recording_lease_quality_checks(paths, plan)
        quality_issues = [*scientific_issues, *lease_issues]
        quality_metrics = {**scientific_metrics, **lease_metrics}
        if quality_issues:
            report = XdfValidationReport(
                ok=False,
                issues=tuple([*report.issues, *quality_issues]),
                checked_artifacts=report.checked_artifacts,
                checked_streams=report.checked_streams,
                metrics={**dict(report.metrics), **quality_metrics},
            )
        elif quality_metrics:
            report = XdfValidationReport(
                ok=report.ok,
                issues=report.issues,
                checked_artifacts=report.checked_artifacts,
                checked_streams=report.checked_streams,
                metrics={**dict(report.metrics), **quality_metrics},
            )
        return inspections, report

    def merge(self, paths: ArtifactPaths, *, command_id: str) -> dict[str, Any]:
        artifacts = self.source_artifacts(paths)
        if not artifacts:
            raise RecordingRuntimeError("no frozen XDF source artifacts were found")
        backend = self._backend_for_merge(paths)
        response = backend.merge(
            [path for _source_key, path in artifacts],
            paths.merged_xdf,
            command_id=command_id,
        )
        _require_worker_ok(response.ok, response.error, "merge XDF")
        if not paths.merged_xdf.is_file():
            raise RecordingRuntimeError("worker reported a merge but derived/session.xdf is missing")
        return dict(response.result)

    def inspect_merge(self, paths: ArtifactPaths) -> tuple[XdfArtifactInspection, Any]:
        source_inspections, source_report = self.inspect_sources(paths)
        if not source_report.ok:
            return (
                PyXdfInspector().inspect(paths.merged_xdf, source_key="merged", merged_artifact=True),
                source_report,
            )
        merged = PyXdfInspector().inspect(paths.merged_xdf, source_key="merged", merged_artifact=True)
        return merged, validate_merge_parity(source_inspections, merged)

    def shutdown_worker(self, paths: ArtifactPaths) -> dict[str, Any]:
        """Best-effort post-merge shutdown; scientific completion stays valid."""

        endpoint = WorkerStateStore(paths.worker_state_file).load()
        if endpoint is None:
            return {"ok": True, "already_stopped": True, "reason": "worker_state_missing"}
        coordinator = RecordingCoordinator(
            paths,
            LoopbackWorkerClient(endpoint, timeout_seconds=5.0),
        )
        try:
            response = coordinator.shutdown(
                command_id=f"shutdown-{paths.identity.session_id}-g{endpoint.generation}"
            )
            if not response.ok:
                return {"ok": False, "warning": response.error or "worker rejected shutdown"}
            plan = self._load_plan(paths)
            plan.update(
                worker_shutdown_requested_at_epoch=self._clock(),
                worker_shutdown_generation=endpoint.generation,
            )
            atomic_write_json(paths.root / "recording-plan.json", plan)
            return {"ok": True, **dict(response.result)}
        except Exception as error:
            return {"ok": False, "warning": f"{type(error).__name__}: {error}"}

    def _backend_for(self, paths: ArtifactPaths) -> NativeWorkerXdfBackend:
        endpoint = WorkerStateStore(paths.worker_state_file).load()
        if endpoint is None:
            raise RecordingRuntimeError("recording worker state is missing")
        return NativeWorkerXdfBackend(
            RecordingCoordinator(paths, LoopbackWorkerClient(endpoint, timeout_seconds=15.0))
        )

    def _backend_for_merge(self, paths: ArtifactPaths) -> NativeWorkerXdfBackend:
        """Return a live merge-capable worker, restarting only the control plane.

        A worker may die after every source was already frozen. Starting a new
        recording generation in that state would invent empty ``part-NNNN``
        artifacts. Instead, a replacement generation is launched without any
        source commands and is used only for the deterministic merge. Native
        workers must fence themselves by observing the generation in the
        atomically replaced worker-state file.
        """

        endpoint = WorkerStateStore(paths.worker_state_file).load()
        if endpoint is None:
            raise RecordingRuntimeError("recording worker state is missing")
        try:
            client = self._healthy_client(paths, endpoint)
        except Exception as error:
            plan = self._load_plan(paths)
            if plan.get("status") != "frozen":
                raise RecordingRuntimeError(
                    "a dead recording worker may be replaced for merge only after freeze"
                ) from error
            availability = self.locator.locate()
            if not self._availability_can_start(availability):
                raise RecordingRuntimeError(
                    availability.reason or "native XDF worker is unavailable for merge recovery"
                ) from error
            generation = endpoint.generation + 1
            launcher = self._launcher_factory(
                WorkerLaunchSpec(availability=availability, resource_root=self.resource_root)
            )
            endpoint, client = launcher.launch(paths, generation=generation)
            plan.update(
                worker=endpoint.public_dict(),
                merge_worker_generation=generation,
                merge_worker_recovered_at_epoch=self._clock(),
                merge_worker_recovery_reason=str(error),
            )
            atomic_write_json(paths.root / "recording-plan.json", plan)

        # A lossless merge can legitimately exceed the short command timeout
        # used for health/freeze operations. The worker still journals the
        # command before starting it, so an ambiguous transport retry must
        # reuse the same command id rather than launch another merge.
        client.timeout_seconds = 15 * 60.0
        return NativeWorkerXdfBackend(RecordingCoordinator(paths, client))

    def _availability_can_start(self, availability: WorkerBinaryAvailability) -> bool:
        if (
            availability.available
            and availability.kind == "hybrid_core"
            and availability.core_path is not None
            and availability.canonical_xdf
            and availability.supports_merge
        ):
            return True
        return bool(
            self._allow_legacy_test_worker
            and availability.available
            and availability.kind == "legacy_external_worker"
            and availability.path is not None
        )

    def _load_plan(self, paths: ArtifactPaths) -> dict[str, Any]:
        plan = _read_object(paths.root / "recording-plan.json")
        if plan.get("schema") != RECORDING_PLAN_SCHEMA:
            raise RecordingRuntimeError("recording plan is missing or invalid")
        return plan

    def _find_paths(self, session_id: str) -> ArtifactPaths | None:
        session_key = str(session_id or "").strip()
        root = self._active_paths.get(session_key)
        candidates: Iterable[Path]
        if root is not None:
            candidates = (root / "session-identity.json",)
        else:
            candidates = self.data_dir.glob("*/participants/*/sessions/*/session-identity.json")
        for identity_file in candidates:
            try:
                payload = _read_object(identity_file)
            except Exception:
                continue
            if str(payload.get("session_id") or "") != session_key:
                continue
            identity = SessionIdentity(
                study_id=str(payload["study_id"]),
                participant_id=str(payload["participant_id"]),
                session_id=session_key,
                started_at=_parse_utc(str(payload["started_at"])),
            )
            paths = self.artifacts.paths_for(identity)
            self._active_paths[session_key] = paths.root
            return paths
        return None


