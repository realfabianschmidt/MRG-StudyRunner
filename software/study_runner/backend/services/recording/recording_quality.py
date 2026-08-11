"""Scientific source, backup, lease, and finalization quality checks."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable, Mapping

from study_runner.recording import markers
from study_runner.recording.artifacts import ArtifactPaths
from study_runner.recording.backup import BackupSampler, projections_from_manifest
from study_runner.recording.recovery import RecordingLeaseStore
from study_runner.recording.xdf import ValidationIssue, XdfArtifactInspection
from .recording_contract import RecordingContractError, load_recording_contract
from .recording_dependencies import get_plugin_manifests_with_internal_sources
from .recording_runtime_support import RecordingRuntimeError, read_object


def validation_details(
    report: Any,
    *,
    inspections: Iterable[XdfArtifactInspection],
) -> dict[str, Any]:
    return {
        "ok": bool(report.ok),
        "checked_artifacts": int(report.checked_artifacts),
        "checked_streams": int(report.checked_streams),
        "metrics": dict(report.metrics),
        "issues": [asdict(issue) for issue in report.issues],
        "artifacts": [
            {
                "source_key": inspection.source_key,
                "path": str(inspection.path),
                "readable": inspection.readable,
                "file_sha256": inspection.file_sha256,
                "stream_count": len(inspection.streams),
                "error": inspection.error,
            }
            for inspection in inspections
        ],
    }


def validation_error(label: str, report: Any) -> str:
    rendered = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues[:8])
    return f"XDF {label} failed: {rendered or 'unknown validation error'}"


def producer_stop_failures(details: Mapping[str, Any]) -> list[str]:
    """Render explicit plugin/coordinator stop failures for finalization."""

    failures: list[str] = []
    for section_name in ("runtime", "coordinator"):
        section = details.get(section_name)
        if not isinstance(section, Mapping):
            continue
        for key, status in section.items():
            if isinstance(status, Mapping) and status.get("ok") is False:
                failures.append(
                    f"{section_name}.{key}: "
                    f"{status.get('error') or status.get('reason') or 'stop failed'}"
                )
    return failures


def recording_lease_quality_checks(
    paths: ArtifactPaths,
    plan: Mapping[str, Any],
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Make autonomous/expired worker closure a mandatory quality issue."""

    issues: list[ValidationIssue] = []
    metrics: dict[str, Any] = {}
    lease = None
    if paths.recording_lease_file.is_file():
        try:
            lease = RecordingLeaseStore(paths.recording_lease_file).load()
        except Exception as error:
            issues.append(
                ValidationIssue(
                    code="recording_lease_unreadable",
                    message=f"recording lease metadata is unreadable: {error}",
                    source_key="recording_worker",
                )
            )
    if lease is not None:
        metrics["recording_lease"] = lease.as_dict()
        if lease.state == "expired" or lease.close_reason == "web_server_lease_expired":
            issues.append(
                ValidationIssue(
                    code="web_server_lease_expired",
                    message=(
                        "the recording worker closed after the 15-minute web-server lease; "
                        "an administrator must confirm degraded completion"
                    ),
                    source_key="recording_worker",
                )
            )

    attention_path = paths.root / "recording-worker-attention.json"
    attention: Mapping[str, Any] | None = None
    if attention_path.is_file():
        try:
            payload = read_object(attention_path)
            attention = payload
            metrics["recording_worker_attention"] = dict(payload)
        except RecordingRuntimeError as error:
            issues.append(
                ValidationIssue(
                    code="recording_worker_attention_unreadable",
                    message=str(error),
                    source_key="recording_worker",
                )
            )
    attention_reason = str((attention or {}).get("reason") or "")
    plan_reason = str(plan.get("worker_freeze_reason") or "")
    if (
        "web_server_lease_expired" in {attention_reason, plan_reason}
        and not any(issue.code == "web_server_lease_expired" for issue in issues)
    ):
        issues.append(
            ValidationIssue(
                code="web_server_lease_expired",
                message=(
                    "the recording worker reported autonomous closure after web-server loss; "
                    "an administrator must confirm degraded completion"
                ),
                source_key="recording_worker",
            )
        )
    return issues, metrics


def scientific_source_checks(
    plan: Mapping[str, Any],
    inspections: Iterable[XdfArtifactInspection],
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Check declared streams, rates, counts, and marker-time coverage."""

    artifacts = list(inspections)
    required = set(str(key) for key in (plan.get("required_source_keys") or []))
    try:
        contract = load_recording_contract(plan)
    except RecordingContractError as error:
        issues = [
            ValidationIssue(
                code="recording_contract_invalid",
                message=str(error),
            )
        ]
        backup_metrics = backup_source_checks(plan, artifacts, issues)
        return issues, {
            "recording_contract": {"present": True, "valid": False, "error": str(error)},
            "declared_streams": [],
            "derived_backup": backup_metrics,
        }

    if contract is None:
        manifests = get_plugin_manifests_with_internal_sources()
        streams_by_source = {
            plugin_key: list((manifest or {}).get("streams") or [])
            for plugin_key, manifest in manifests.items()
        }
        contract_metrics = {"present": False, "valid": None, "legacy_fallback": True}
        quality_plan: Mapping[str, Any] = plan
    else:
        streams_by_source = contract["streams_by_source"]
        contract_metrics = {
            "present": True,
            "valid": True,
            "schema": contract["schema"],
            "sha256": contract["sha256"],
        }
        persisted_backup = plan.get("backup") if isinstance(plan.get("backup"), Mapping) else {}
        quality_plan = {
            **dict(plan),
            "backup": {**dict(persisted_backup), **contract["backup"]},
        }
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for artifact in artifacts:
        for stream in artifact.streams:
            key = (artifact.source_key, stream.source_id)
            item = aggregated.setdefault(
                key,
                {
                    "sample_count": 0,
                    "first_timestamp": None,
                    "last_timestamp": None,
                    "nominal_rate_hz": stream.nominal_srate,
                    "name": stream.name,
                    "timestamps_monotonic": True,
                    "max_gap_seconds": None,
                    "sequence_drop_count": (
                        0 if stream.sequence_drop_count is not None else None
                    ),
                    "segment_bounds": [],
                },
            )
            item["sample_count"] += stream.sample_count
            if stream.first_timestamp is not None:
                item["first_timestamp"] = (
                    stream.first_timestamp
                    if item["first_timestamp"] is None
                    else min(item["first_timestamp"], stream.first_timestamp)
                )
            if stream.last_timestamp is not None:
                item["last_timestamp"] = (
                    stream.last_timestamp
                    if item["last_timestamp"] is None
                    else max(item["last_timestamp"], stream.last_timestamp)
                )
            item["timestamps_monotonic"] = bool(
                item["timestamps_monotonic"] and stream.timestamps_monotonic
            )
            if stream.max_gap_seconds is not None:
                item["max_gap_seconds"] = max(
                    float(item["max_gap_seconds"] or 0.0),
                    float(stream.max_gap_seconds),
                )
            if stream.sequence_drop_count is not None:
                item["sequence_drop_count"] = int(item.get("sequence_drop_count") or 0) + int(
                    stream.sequence_drop_count
                )
            if stream.first_timestamp is not None and stream.last_timestamp is not None:
                item["segment_bounds"].append(
                    (stream.first_timestamp, stream.last_timestamp)
                )

    for item in aggregated.values():
        bounds = sorted(item.pop("segment_bounds", []))
        for previous, current in zip(bounds, bounds[1:]):
            boundary_gap = float(current[0]) - float(previous[1])
            item["max_gap_seconds"] = max(
                float(item["max_gap_seconds"] or 0.0),
                boundary_gap,
            )

    marker_keys = {markers.SOURCE_KEY}
    marker_ranges = [
        item
        for (source_key, _source_id), item in aggregated.items()
        if source_key in marker_keys
        and item["first_timestamp"] is not None
        and item["last_timestamp"] is not None
    ]
    marker_start = min((item["first_timestamp"] for item in marker_ranges), default=None)
    marker_end = max((item["last_timestamp"] for item in marker_ranges), default=None)
    issues: list[ValidationIssue] = []
    metrics: list[dict[str, Any]] = []
    for plugin_key in sorted(required):
        for declared in streams_by_source.get(plugin_key) or []:
            source_id = str(declared.get("source_id") or "")
            key = (plugin_key, source_id)
            actual = aggregated.get(key)
            if actual is None:
                issues.append(
                    ValidationIssue(
                        code="missing_declared_stream",
                        message=f"declared stream {source_id!r} is absent",
                        source_key=plugin_key,
                    )
                )
                continue
            expected_rate = float(declared.get("nominal_rate_hz") or 0.0)
            expected_count = None
            missing_count = None
            sample_coverage = None
            if (
                expected_rate > 0
                and marker_start is not None
                and marker_end is not None
                and marker_end >= marker_start
            ):
                expected_count = max(1, int(round((marker_end - marker_start) * expected_rate)))
                missing_count = max(0, expected_count - int(actual["sample_count"]))
                sample_coverage = min(1.0, int(actual["sample_count"]) / expected_count)
            metrics.append(
                {
                    "source_key": plugin_key,
                    "source_id": source_id,
                    "nominal_rate_hz": actual["nominal_rate_hz"],
                    "sample_count": actual["sample_count"],
                    "first_timestamp": actual["first_timestamp"],
                    "last_timestamp": actual["last_timestamp"],
                    "expected_sample_count": expected_count,
                    "missing_sample_count": missing_count,
                    "sample_coverage": sample_coverage,
                    "sequence_drop_count": actual["sequence_drop_count"],
                    "max_gap_seconds": actual["max_gap_seconds"],
                    "timestamps_monotonic": actual["timestamps_monotonic"],
                }
            )
            if actual["sample_count"] < 1:
                issues.append(
                    ValidationIssue(
                        code="empty_declared_stream",
                        message=f"declared stream {source_id!r} contains no samples",
                        source_key=plugin_key,
                    )
                )
            if expected_rate > 0 and abs(float(actual["nominal_rate_hz"]) - expected_rate) > 1e-9:
                issues.append(
                    ValidationIssue(
                        code="nominal_rate_mismatch",
                        message=(
                            f"stream {source_id!r} reports {actual['nominal_rate_hz']} Hz; "
                            f"manifest declares {expected_rate} Hz"
                        ),
                        source_key=plugin_key,
                    )
                )
            if not actual["timestamps_monotonic"]:
                issues.append(
                    ValidationIssue(
                        code="non_monotonic_timestamps",
                        message=f"stream {source_id!r} contains decreasing raw timestamps",
                        source_key=plugin_key,
                    )
                )
            if sample_coverage is not None and sample_coverage < 0.5:
                issues.append(
                    ValidationIssue(
                        code="severe_sample_loss",
                        message=(
                            f"stream {source_id!r} contains only {sample_coverage:.1%} "
                            "of its marker-window expected samples"
                        ),
                        source_key=plugin_key,
                    )
                )
            if (
                plugin_key != "lsl"
                and expected_rate > 0
                and marker_start is not None
                and marker_end is not None
                and (
                    actual["first_timestamp"] is None
                    or actual["last_timestamp"] is None
                    or actual["first_timestamp"] > marker_start + 1.0 / expected_rate
                    or actual["last_timestamp"] < marker_end - 1.0 / expected_rate
                )
            ):
                issues.append(
                    ValidationIssue(
                        code="insufficient_time_coverage",
                        message=(
                            f"stream {source_id!r} does not cover the marker-defined session window"
                        ),
                        source_key=plugin_key,
                    )
                )
    backup_metrics = backup_source_checks(quality_plan, artifacts, issues)
    return issues, {
        "recording_contract": contract_metrics,
        "declared_streams": metrics,
        "derived_backup": backup_metrics,
    }


def backup_source_checks(
    plan: Mapping[str, Any],
    artifacts: list[XdfArtifactInspection],
    issues: list[ValidationIssue],
) -> dict[str, Any]:
    backup = plan.get("backup")
    if not isinstance(backup, Mapping):
        return {"configured": False}
    streams = [
        stream
        for artifact in artifacts
        if artifact.source_key == "derived_backup" and artifact.readable
        for stream in artifact.streams
    ]
    expected_rate = float(backup.get("rate_hz") or 0.0)
    expected_strategy = str(backup.get("resampling_strategy") or "")
    expected_plugins = {str(value) for value in backup.get("active_plugins") or []}
    expected_channel_names = set(expected_backup_channel_names(backup))
    expected_quality = {
        label for label in expected_channel_names if is_backup_quality_channel(label)
    }
    expected_outputs = expected_channel_names - expected_quality
    labels = {label for stream in streams for label in stream.channel_labels}

    if not streams:
        issues.append(
            ValidationIssue(
                code="missing_backup_stream",
                message="derived backup XDF contains no readable stream",
                source_key="derived_backup",
            )
        )
    for stream in streams:
        if abs(stream.nominal_srate - expected_rate) > 1e-9:
            issues.append(
                ValidationIssue(
                    code="backup_rate_mismatch",
                    message=(
                        f"derived backup reports {stream.nominal_srate} Hz; "
                        f"recording plan requires {expected_rate} Hz"
                    ),
                    source_key="derived_backup",
                    origin_id=stream.origin_id,
                )
            )
        if stream.artifact_role != "derived_backup":
            issues.append(
                ValidationIssue(
                    code="backup_role_missing",
                    message="derived backup stream lacks artifact_role=derived_backup",
                    source_key="derived_backup",
                    origin_id=stream.origin_id,
                )
            )
        if stream.resampling_strategy != expected_strategy:
            issues.append(
                ValidationIssue(
                    code="backup_strategy_mismatch",
                    message="derived backup resampling strategy differs from the recording plan",
                    source_key="derived_backup",
                    origin_id=stream.origin_id,
                )
            )
        if expected_plugins and not expected_plugins.issubset(set(stream.active_plugins)):
            issues.append(
                ValidationIssue(
                    code="backup_plugins_missing",
                    message="derived backup metadata omits active recording plugins",
                    source_key="derived_backup",
                    origin_id=stream.origin_id,
                )
            )
        if stream.invalid_rows_with_values:
            issues.append(
                ValidationIssue(
                    code="backup_forward_fill_detected",
                    message=(
                        "invalid backup rows contain finite projected values instead of NaN"
                    ),
                    source_key="derived_backup",
                    origin_id=stream.origin_id,
                )
            )

    missing_outputs = sorted(expected_outputs - labels)
    missing_quality = sorted(expected_quality - labels)
    if missing_outputs:
        issues.append(
            ValidationIssue(
                code="backup_projection_channels_missing",
                message="derived backup omits projections: " + ", ".join(missing_outputs),
                source_key="derived_backup",
            )
        )
    if missing_quality:
        issues.append(
            ValidationIssue(
                code="backup_quality_channels_missing",
                message="derived backup omits QC channels: " + ", ".join(missing_quality),
                source_key="derived_backup",
            )
        )
    return {
        "configured": True,
        "stream_count": len(streams),
        "rate_hz": expected_rate,
        "expected_channel_names": sorted(expected_channel_names),
        "expected_projection_channels": sorted(expected_outputs),
        "missing_projection_channels": missing_outputs,
        "missing_quality_channels": missing_quality,
        "invalid_rows_with_values": sum(
            stream.invalid_rows_with_values for stream in streams
        ),
    }


def expected_backup_channel_names(backup: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the exact fully-qualified order emitted by ``BackupSampler``."""

    projection_objects = []
    for raw_projection in backup.get("projections") or []:
        if not isinstance(raw_projection, Mapping):
            continue
        plugin_key = str(raw_projection.get("plugin_key") or "").strip()
        if not plugin_key:
            continue
        try:
            projection_objects.extend(projections_from_manifest(plugin_key, raw_projection))
        except (TypeError, ValueError):
            continue
    if projection_objects:
        return BackupSampler(tuple(projection_objects), start_monotonic=0.0).channel_names

    declared = backup.get("channel_names")
    if not isinstance(declared, list):
        return ()
    return tuple(
        dict.fromkeys(
            str(label).strip()
            for label in declared
            if isinstance(label, str) and label.strip()
        )
    )


def is_backup_quality_channel(label: str) -> bool:
    suffix = str(label or "").casefold().rsplit(".", 1)[-1]
    return suffix in {"valid", "sample_age_ms", "sequence", "status"}
