"""Canonical XDF backend boundary and lossless-merge validation contracts.

``pyxdf`` is intentionally used only as an importer/validator.  This module
does not implement XDF chunk encoding or byte concatenation.  Production XDF
writing/merging is delegated to the bundled native worker.  The Python
fallback writes a differently suffixed recovery journal and reports
``canonical_xdf=False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .artifacts import sha256_file
from .coordinator import RecordingCoordinator
from .errors import XdfBackendUnavailableError
from .worker_protocol import WorkerResponse


PINNED_PYXDF_VERSION = "1.16.8"
PINNED_NUMPY_VERSION = "1.26.4"
RECOVERY_JOURNAL_SCHEMA = "study-runner/xdf-recovery-journal/v1"


@dataclass(frozen=True)
class XdfBackendStatus:
    name: str
    available: bool
    canonical_xdf: bool
    supports_merge: bool
    reason: str | None = None


class CanonicalXdfBackend(Protocol):
    def status(self) -> XdfBackendStatus: ...

    def start_source(
        self,
        plugin_key: str,
        streams: Sequence[Mapping[str, Any]],
        *,
        command_id: str,
        require_stream_headers: bool = True,
        require_fresh_primary_sample: bool = False,
    ) -> WorkerResponse: ...

    def freeze(self, *, command_id: str) -> WorkerResponse: ...

    def merge(
        self,
        source_paths: Iterable[Path],
        output_path: Path,
        *,
        command_id: str,
    ) -> WorkerResponse: ...


class NativeWorkerXdfBackend:
    """Production adapter for a packaged worker using official XDFWriter code."""

    def __init__(self, coordinator: RecordingCoordinator) -> None:
        self.coordinator = coordinator

    def status(self) -> XdfBackendStatus:
        endpoint = self.coordinator.worker.endpoint
        return XdfBackendStatus(
            name=endpoint.backend_name,
            available=True,
            canonical_xdf=True,
            supports_merge=True,
        )

    def start_source(
        self,
        plugin_key: str,
        streams: Sequence[Mapping[str, Any]],
        *,
        command_id: str,
        require_stream_headers: bool = True,
        require_fresh_primary_sample: bool = False,
    ) -> WorkerResponse:
        return self.coordinator.start_plugin(
            plugin_key,
            streams,
            command_id=command_id,
            require_stream_headers=require_stream_headers,
            require_fresh_primary_sample=require_fresh_primary_sample,
        )

    def freeze(self, *, command_id: str) -> WorkerResponse:
        return self.coordinator.freeze(command_id=command_id)

    def merge(
        self,
        source_paths: Iterable[Path],
        output_path: Path,
        *,
        command_id: str,
    ) -> WorkerResponse:
        return self.coordinator.merge(source_paths, output_path, command_id=command_id)


class UnavailableCanonicalXdfBackend:
    """Fail-closed backend used when no bundled native worker is present."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def status(self) -> XdfBackendStatus:
        return XdfBackendStatus(
            name="unavailable",
            available=False,
            canonical_xdf=False,
            supports_merge=False,
            reason=self.reason,
        )

    def start_source(
        self,
        plugin_key: str,
        streams: Sequence[Mapping[str, Any]],
        *,
        command_id: str,
    ) -> WorkerResponse:
        raise XdfBackendUnavailableError(self.reason)

    def freeze(self, *, command_id: str) -> WorkerResponse:
        raise XdfBackendUnavailableError(self.reason)

    def merge(
        self,
        source_paths: Iterable[Path],
        output_path: Path,
        *,
        command_id: str,
    ) -> WorkerResponse:
        raise XdfBackendUnavailableError(self.reason)


class RecoveryJournalWriter:
    """Append-only, fsynced emergency journal which is explicitly not XDF."""

    def __init__(self, path: Path, metadata: Mapping[str, Any]) -> None:
        self.path = Path(path)
        if self.path.suffix.lower() == ".xdf":
            raise ValueError("recovery journals may never use the .xdf suffix")
        if not self.path.name.endswith(".recovery.jsonl"):
            raise ValueError("recovery journal must end in .recovery.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._handle = self.path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError as error:
            raise FileExistsError(f"refusing to append an existing recovery journal: {self.path}") from error
        self._closed = False
        self._write_line(
            {
                "schema": RECOVERY_JOURNAL_SCHEMA,
                "record_type": "header",
                "canonical_xdf": False,
                "metadata": _strict_json_value(metadata),
            }
        )

    def append(
        self,
        *,
        stream_id: str,
        source_timestamp: float | None,
        received_monotonic: float,
        sequence: int | None,
        values: Sequence[Any],
    ) -> None:
        if self._closed:
            raise RuntimeError("recovery journal is closed")
        self._write_line(
            {
                "record_type": "sample",
                "stream_id": stream_id,
                "source_timestamp": _strict_json_value(source_timestamp),
                "received_monotonic": _strict_json_value(received_monotonic),
                "sequence": sequence,
                "values": _strict_json_value(list(values)),
            }
        )

    def close(self, *, reason: str = "ordered_close") -> None:
        if self._closed:
            return
        self._write_line({"record_type": "footer", "reason": reason})
        self._handle.close()
        self._closed = True

    def _write_line(self, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._handle.write(encoded + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def __enter__(self) -> "RecoveryJournalWriter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close(reason="exception" if exc_type else "ordered_close")


class PythonRecoveryJournalBackend:
    """Safe fallback preserving samples without ever claiming XDF support."""

    def status(self) -> XdfBackendStatus:
        return XdfBackendStatus(
            name="python-recovery-journal",
            available=True,
            canonical_xdf=False,
            supports_merge=False,
            reason="Bundled native XDF worker unavailable; recovery JSONL only",
        )

    def open_for_requested_xdf(
        self,
        requested_xdf_path: Path,
        *,
        metadata: Mapping[str, Any],
    ) -> RecoveryJournalWriter:
        requested = Path(requested_xdf_path)
        if requested.suffix.lower() != ".xdf":
            raise ValueError("requested canonical recording target must have an .xdf suffix")
        journal_path = requested.with_suffix(".recovery.jsonl")
        return RecoveryJournalWriter(journal_path, metadata)


@dataclass(frozen=True)
class DependencyStatus:
    ok: bool
    installed: Mapping[str, str | None]
    expected: Mapping[str, str]
    reason: str | None = None


def validator_dependency_status() -> DependencyStatus:
    installed: dict[str, str | None] = {}
    expected = {"pyxdf": PINNED_PYXDF_VERSION, "numpy": PINNED_NUMPY_VERSION}
    problems: list[str] = []
    for package, version in expected.items():
        try:
            installed_version = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            installed_version = None
        installed[package] = installed_version
        if installed_version != version:
            problems.append(f"{package}={installed_version or 'missing'} (expected {version})")
    return DependencyStatus(
        ok=not problems,
        installed=installed,
        expected=expected,
        reason="; ".join(problems) if problems else None,
    )


@dataclass(frozen=True)
class StreamInspection:
    origin_id: str
    name: str
    stream_type: str
    source_id: str
    nominal_srate: float
    channel_count: int
    sample_count: int
    first_timestamp: float | None
    last_timestamp: float | None
    sample_hash: str
    timestamp_hash: str
    clock_offsets_hash: str
    metadata_hash: str
    stream_id: str = ""
    timestamps_monotonic: bool = True
    max_gap_seconds: float | None = None
    channel_labels: tuple[str, ...] = ()
    sequence_drop_count: int | None = None
    artifact_role: str = ""
    resampling_strategy: str = ""
    active_plugins: tuple[str, ...] = ()
    invalid_rows_with_values: int = 0
    footer_checked: bool = False
    footer_present: bool = True
    footer_sample_count: int | None = None


@dataclass(frozen=True)
class XdfArtifactInspection:
    path: Path
    source_key: str
    readable: bool
    file_sha256: str | None
    streams: tuple[StreamInspection, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    source_key: str | None = None
    origin_id: str | None = None


@dataclass(frozen=True)
class XdfValidationReport:
    ok: bool
    issues: tuple[ValidationIssue, ...] = ()
    checked_artifacts: int = 0
    checked_streams: int = 0
    metrics: Mapping[str, Any] = field(default_factory=dict)


class PyXdfInspector:
    """Loads raw timestamps with pyxdf; synchronization and dejitter are off."""

    def __init__(self, *, enforce_pinned_dependencies: bool = True) -> None:
        self.dependency_status = validator_dependency_status()
        if enforce_pinned_dependencies and not self.dependency_status.ok:
            raise XdfBackendUnavailableError(
                f"XDF validator dependency mismatch: {self.dependency_status.reason}"
            )

    def inspect(
        self,
        path: Path,
        *,
        source_key: str,
        merged_artifact: bool = False,
    ) -> XdfArtifactInspection:
        target = Path(path)
        if not target.is_file():
            return XdfArtifactInspection(
                path=target,
                source_key=source_key,
                readable=False,
                file_sha256=None,
                error="file does not exist",
            )
        try:
            import pyxdf

            streams, _header = pyxdf.load_xdf(
                str(target),
                synchronize_clocks=False,
                handle_clock_resets=False,
                dejitter_timestamps=False,
                verbose=False,
            )
            inspected = tuple(
                self._inspect_stream(
                    stream,
                    generated_origin=stream_origin_id(source_key, target, index),
                    require_embedded_origin=merged_artifact,
                )
                for index, stream in enumerate(streams)
            )
            return XdfArtifactInspection(
                path=target,
                source_key=source_key,
                readable=True,
                file_sha256=sha256_file(target),
                streams=inspected,
            )
        except Exception as error:
            return XdfArtifactInspection(
                path=target,
                source_key=source_key,
                readable=False,
                file_sha256=_best_effort_sha256(target),
                error=f"{type(error).__name__}: {error}",
            )

    @staticmethod
    def _inspect_stream(
        stream: Mapping[str, Any],
        *,
        generated_origin: str,
        require_embedded_origin: bool,
    ) -> StreamInspection:
        info = stream.get("info") if isinstance(stream.get("info"), dict) else {}
        embedded_origin = _find_metadata_value(info, "study_runner_origin_id")
        if require_embedded_origin and not embedded_origin:
            raise ValueError("merged stream is missing study_runner_origin_id provenance")
        origin_id = embedded_origin or generated_origin
        series = stream.get("time_series", [])
        timestamps = stream.get("time_stamps", [])
        sample_count = len(timestamps)
        first_timestamp = float(timestamps[0]) if sample_count else None
        last_timestamp = float(timestamps[-1]) if sample_count else None
        channel_count = _integer_info(info, "channel_count")
        if channel_count < 1:
            channel_count = _series_channel_count(series)
        clock_payload = {
            "clock_times": _plain_value(stream.get("clock_times", [])),
            "clock_values": _plain_value(stream.get("clock_values", [])),
        }
        timestamp_values = _numeric_values(timestamps)
        monotonic, max_gap = _timestamp_quality(timestamp_values)
        channel_labels = _channel_labels(info)
        footer = stream.get("footer") if isinstance(stream.get("footer"), Mapping) else None
        # pyxdf 1.16.8 preserves the StreamFooter XML root, so footer fields
        # live below ``footer["info"]``.  Older/importer-specific fixtures may
        # expose the fields directly; accepting both shapes keeps validation
        # strict without mistaking a complete native XDF for a missing count.
        nested_footer_info = footer.get("info") if footer else None
        footer_info = (
            nested_footer_info
            if isinstance(nested_footer_info, Mapping)
            else footer
        )
        return StreamInspection(
            origin_id=origin_id,
            name=_string_info(info, "name"),
            stream_type=_string_info(info, "type"),
            source_id=_string_info(info, "source_id"),
            nominal_srate=_float_info(info, "nominal_srate"),
            channel_count=channel_count,
            sample_count=sample_count,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            sample_hash=_hash_samples(series),
            timestamp_hash=_hash_numeric(timestamps),
            clock_offsets_hash=_hash_json(clock_payload),
            metadata_hash=_metadata_hash(info),
            stream_id=_string_info(info, "stream_id"),
            timestamps_monotonic=monotonic,
            max_gap_seconds=max_gap,
            channel_labels=channel_labels,
            sequence_drop_count=_sequence_drop_count(series, channel_labels),
            artifact_role=_find_metadata_value(info, "artifact_role"),
            resampling_strategy=_find_metadata_value(info, "resampling_strategy"),
            active_plugins=_metadata_string_list(info, "active_plugins"),
            invalid_rows_with_values=_invalid_rows_with_values(series, channel_labels),
            footer_checked=True,
            footer_present=bool(footer),
            footer_sample_count=_optional_integer_info(footer_info or {}, "sample_count"),
        )


def validate_sources(
    inspections: Sequence[XdfArtifactInspection],
    *,
    required_source_keys: Iterable[str] = (),
) -> XdfValidationReport:
    issues: list[ValidationIssue] = []
    readable_keys: set[str] = set()
    all_origins: set[str] = set()
    stream_count = 0
    for inspection in inspections:
        if not inspection.readable:
            issues.append(
                ValidationIssue(
                    code="unreadable_source",
                    message=inspection.error or "XDF source is unreadable",
                    source_key=inspection.source_key,
                )
            )
            continue
        if not inspection.streams:
            issues.append(
                ValidationIssue(
                    code="empty_source",
                    message="XDF source contains no streams",
                    source_key=inspection.source_key,
                )
            )
            continue
        readable_keys.add(inspection.source_key)
        stream_count += len(inspection.streams)
        seen_origins: set[str] = set()
        for stream in inspection.streams:
            if stream.footer_checked and not stream.footer_present:
                issues.append(
                    ValidationIssue(
                        code="source_footer_missing",
                        message="XDF stream has no readable footer; the final chunk may be truncated",
                        source_key=inspection.source_key,
                        origin_id=stream.origin_id,
                    )
                )
            elif stream.footer_checked and stream.footer_sample_count is None:
                issues.append(
                    ValidationIssue(
                        code="source_footer_sample_count_missing",
                        message="XDF stream footer has no sample_count",
                        source_key=inspection.source_key,
                        origin_id=stream.origin_id,
                    )
                )
            elif stream.footer_checked and stream.footer_sample_count != stream.sample_count:
                issues.append(
                    ValidationIssue(
                        code="source_footer_sample_count_mismatch",
                        message=(
                            f"XDF footer declares {stream.footer_sample_count} samples but "
                            f"PyXDF read {stream.sample_count}"
                        ),
                        source_key=inspection.source_key,
                        origin_id=stream.origin_id,
                    )
                )
            if stream.origin_id in seen_origins:
                issues.append(
                    ValidationIssue(
                        code="duplicate_origin",
                        message="source contains a duplicate stream origin id",
                        source_key=inspection.source_key,
                        origin_id=stream.origin_id,
                    )
                )
            seen_origins.add(stream.origin_id)
            if stream.origin_id in all_origins:
                issues.append(
                    ValidationIssue(
                        code="duplicate_origin_across_sources",
                        message="stream origin id occurs in more than one source artifact",
                        source_key=inspection.source_key,
                        origin_id=stream.origin_id,
                    )
                )
            all_origins.add(stream.origin_id)

    for required in sorted(set(required_source_keys)):
        if required not in readable_keys:
            issues.append(
                ValidationIssue(
                    code="missing_required_source",
                    message=f"required recording source {required!r} is missing or unreadable",
                    source_key=required,
                )
            )
    return XdfValidationReport(
        ok=not issues,
        issues=tuple(issues),
        checked_artifacts=len(inspections),
        checked_streams=stream_count,
    )


def validate_merge_parity(
    sources: Sequence[XdfArtifactInspection],
    merged: XdfArtifactInspection,
) -> XdfValidationReport:
    """Require one exactly equivalent merged stream for every source stream."""

    issues: list[ValidationIssue] = []
    if not merged.readable:
        return XdfValidationReport(
            ok=False,
            issues=(
                ValidationIssue(
                    code="unreadable_merge",
                    message=merged.error or "merged XDF is unreadable",
                    source_key=merged.source_key,
                ),
            ),
            checked_artifacts=len(sources) + 1,
        )

    source_streams: dict[str, StreamInspection] = {}
    for artifact in sources:
        if not artifact.readable:
            issues.append(
                ValidationIssue(
                    code="unreadable_source",
                    message=artifact.error or "source XDF is unreadable",
                    source_key=artifact.source_key,
                )
            )
            continue
        for stream in artifact.streams:
            if stream.origin_id in source_streams:
                issues.append(
                    ValidationIssue(
                        code="duplicate_source_origin",
                        message="origin id occurs in more than one source stream",
                        source_key=artifact.source_key,
                        origin_id=stream.origin_id,
                    )
                )
            else:
                source_streams[stream.origin_id] = stream

    merged_streams: dict[str, StreamInspection] = {}
    merged_stream_ids: set[str] = set()
    for stream in merged.streams:
        if not stream.stream_id:
            issues.append(
                ValidationIssue(
                    code="missing_merged_stream_id",
                    message="merged stream has no stream_id",
                    source_key=merged.source_key,
                    origin_id=stream.origin_id,
                )
            )
        elif stream.stream_id in merged_stream_ids:
            issues.append(
                ValidationIssue(
                    code="duplicate_merged_stream_id",
                    message="merged XDF stream_id values are not conflict-free",
                    source_key=merged.source_key,
                    origin_id=stream.origin_id,
                )
            )
        merged_stream_ids.add(stream.stream_id)
        if stream.origin_id in merged_streams:
            issues.append(
                ValidationIssue(
                    code="duplicate_merged_origin",
                    message="origin id occurs more than once in merged XDF",
                    source_key=merged.source_key,
                    origin_id=stream.origin_id,
                )
            )
        else:
            merged_streams[stream.origin_id] = stream

    for origin_id, source in source_streams.items():
        candidate = merged_streams.get(origin_id)
        if candidate is None:
            issues.append(
                ValidationIssue(
                    code="missing_merged_stream",
                    message="source stream is absent from merged XDF",
                    origin_id=origin_id,
                )
            )
            continue
        _compare_streams(source, candidate, issues)

    for origin_id in sorted(merged_streams.keys() - source_streams.keys()):
        issues.append(
            ValidationIssue(
                code="unexpected_merged_stream",
                message="merged XDF contains a stream not declared by its sources",
                origin_id=origin_id,
            )
        )

    return XdfValidationReport(
        ok=not issues,
        issues=tuple(issues),
        checked_artifacts=len(sources) + 1,
        checked_streams=len(source_streams),
        metrics={
            "source_streams": len(source_streams),
            "merged_streams": len(merged_streams),
            "parity_streams": len(source_streams) - sum(issue.code == "missing_merged_stream" for issue in issues),
        },
    )


def _compare_streams(
    source: StreamInspection,
    merged: StreamInspection,
    issues: list[ValidationIssue],
) -> None:
    fields = (
        "name",
        "stream_type",
        "source_id",
        "nominal_srate",
        "channel_count",
        "sample_count",
        "first_timestamp",
        "last_timestamp",
        "sample_hash",
        "timestamp_hash",
        "clock_offsets_hash",
        "metadata_hash",
        "timestamps_monotonic",
        "max_gap_seconds",
        "channel_labels",
        "sequence_drop_count",
        "artifact_role",
        "resampling_strategy",
        "active_plugins",
        "invalid_rows_with_values",
    )
    for field_name in fields:
        if getattr(source, field_name) != getattr(merged, field_name):
            issues.append(
                ValidationIssue(
                    code=f"parity_{field_name}",
                    message=f"merged stream does not preserve {field_name}",
                    origin_id=source.origin_id,
                )
            )


def _numeric_values(value: Any) -> list[float]:
    plain = _plain_value(value)
    try:
        return [float(item) for item in plain]
    except (TypeError, ValueError):
        return []


def _timestamp_quality(values: Sequence[float]) -> tuple[bool, float | None]:
    if len(values) < 2:
        return True, None
    gaps = [right - left for left, right in zip(values, values[1:])]
    return all(gap >= 0 for gap in gaps), max(gaps)


def _channel_labels(info: Mapping[str, Any]) -> tuple[str, ...]:
    def walk(value: Any) -> list[str]:
        if isinstance(value, dict):
            channel_value = value.get("channel")
            if isinstance(channel_value, list):
                labels = []
                for channel in channel_value:
                    if not isinstance(channel, dict):
                        continue
                    label = channel.get("label") or channel.get("name")
                    while isinstance(label, list) and label:
                        label = label[0]
                    labels.append(str(label or ""))
                if labels and all(labels):
                    return labels
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return []

    return tuple(walk(info))


def _series_rows(series: Any) -> list[list[Any]]:
    plain = _plain_value(series)
    if not isinstance(plain, list):
        return []
    rows: list[list[Any]] = []
    for row in plain:
        row = _plain_value(row)
        rows.append(list(row) if isinstance(row, (list, tuple)) else [row])
    return rows


def _sequence_drop_count(series: Any, labels: Sequence[str]) -> int | None:
    indices = [
        index
        for index, label in enumerate(labels)
        if label.casefold() == "sequence" or label.casefold().endswith(".sequence")
    ]
    if not indices:
        return None
    drops = 0
    previous: dict[int, int] = {}
    for row in _series_rows(series):
        for index in indices:
            if index >= len(row):
                continue
            try:
                current = int(row[index])
            except (TypeError, ValueError, OverflowError):
                continue
            if index in previous and current > previous[index] + 1:
                drops += current - previous[index] - 1
            previous[index] = current
    return drops


def _invalid_rows_with_values(series: Any, labels: Sequence[str]) -> int:
    quality_suffixes = {"valid", "sample_age_ms", "sequence", "status"}
    valid_indices_by_prefix: dict[str, list[int]] = {}
    value_indices_by_prefix: dict[str, list[int]] = {}
    for index, label in enumerate(labels):
        prefix, suffix = _channel_prefix_and_suffix(label)
        if suffix == "valid":
            valid_indices_by_prefix.setdefault(prefix, []).append(index)
        elif suffix not in quality_suffixes:
            value_indices_by_prefix.setdefault(prefix, []).append(index)

    if not valid_indices_by_prefix:
        return 0

    violations = 0
    for row in _series_rows(series):
        row_has_violation = False
        for prefix, valid_indices in valid_indices_by_prefix.items():
            invalid = any(
                index < len(row) and _invalid_valid_flag(row[index])
                for index in valid_indices
            )
            if not invalid:
                continue
            for index in value_indices_by_prefix.get(prefix, ()):
                if index >= len(row):
                    continue
                try:
                    numeric = float(row[index])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(numeric):
                    row_has_violation = True
                    break
            if row_has_violation:
                break
        if row_has_violation:
            violations += 1
    return violations


def _channel_prefix_and_suffix(label: str) -> tuple[str, str]:
    normalized = str(label or "").strip().casefold()
    if "." not in normalized:
        return "", normalized
    prefix, suffix = normalized.rsplit(".", 1)
    return prefix, suffix


def _invalid_valid_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value) == 0.0 or not math.isfinite(float(value))
        except (TypeError, ValueError):
            return True
    return str(value).strip().casefold() in {"", "0", "false", "invalid", "missing"}


def _metadata_string_list(info: Mapping[str, Any], key: str) -> tuple[str, ...]:
    raw = _find_metadata_value(info, key).strip()
    if not raw:
        return ()
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        decoded = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(decoded, list):
        return (str(decoded),)
    return tuple(str(item) for item in decoded)


def _hash_samples(value: Any) -> str:
    try:
        import numpy as np

        array = np.asarray(value)
        if array.dtype.kind in "biu":
            if array.dtype.kind == "b":
                numeric = np.asarray(array, dtype="u1")
            else:
                width = max(1, int(array.dtype.itemsize))
                numeric = np.asarray(array, dtype=f"<{array.dtype.kind}{width}")
            shape = json.dumps(list(numeric.shape), separators=(",", ":")).encode("ascii")
            return hashlib.sha256(shape + b"\0" + numeric.tobytes(order="C")).hexdigest()
        if array.dtype.kind == "f":
            numeric = np.asarray(array, dtype="<f8")
            if numeric.size:
                numeric = numeric.copy()
                numeric[np.isnan(numeric)] = np.nan
            shape = json.dumps(list(numeric.shape), separators=(",", ":")).encode("ascii")
            return hashlib.sha256(shape + b"\0" + numeric.tobytes(order="C")).hexdigest()
    except Exception:
        pass
    return _hash_json(_plain_value(value))


def _hash_numeric(value: Any) -> str:
    try:
        import numpy as np

        numeric = np.asarray(value, dtype="<f8")
        if numeric.size:
            numeric = numeric.copy()
            numeric[np.isnan(numeric)] = np.nan
        shape = json.dumps(list(numeric.shape), separators=(",", ":")).encode("ascii")
        return hashlib.sha256(shape + b"\0" + numeric.tobytes(order="C")).hexdigest()
    except Exception:
        return _hash_json(_plain_value(value))


def _metadata_hash(info: Mapping[str, Any]) -> str:
    ignored = {
        "stream_id",
        "effective_srate",
        "study_runner_origin_id",
        "study_runner_plugin_key",
    }

    def normalized_nested(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): normalized_nested(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [normalized_nested(item) for item in value]
        return _plain_value(value)

    return _hash_json(
        {
            str(key): normalized_nested(value)
            for key, value in info.items()
            if str(key) not in ignored
        }
    )


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        _strict_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_json_value(value: Any) -> Any:
    value = _plain_value(value)
    if isinstance(value, dict):
        return {str(key): _strict_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return {"$float": "nan"}
        return {"$float": "positive_infinity" if value > 0 else "negative_infinity"}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _plain_value(value: Any) -> Any:
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _find_metadata_value(value: Any, key: str) -> str:
    if isinstance(value, dict):
        if key in value:
            found = value[key]
            while isinstance(found, list) and found:
                found = found[0]
            if found not in (None, ""):
                return str(found)
        for child in value.values():
            found = _find_metadata_value(child, key)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_metadata_value(child, key)
            if found:
                return found
    return ""


def _string_info(info: Mapping[str, Any], key: str) -> str:
    value = info.get(key, "")
    while isinstance(value, list) and value:
        value = value[0]
    return str(value or "")


def _float_info(info: Mapping[str, Any], key: str) -> float:
    try:
        return float(_string_info(info, key) or 0.0)
    except ValueError:
        return 0.0


def _integer_info(info: Mapping[str, Any], key: str) -> int:
    try:
        return int(float(_string_info(info, key) or 0))
    except ValueError:
        return 0


def _optional_integer_info(info: Mapping[str, Any], key: str) -> int | None:
    value = _string_info(info, key)
    if value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _series_channel_count(series: Any) -> int:
    try:
        if getattr(series, "ndim", 0) >= 2:
            return int(series.shape[1])
        if len(series) and isinstance(series[0], (list, tuple)):
            return len(series[0])
    except (AttributeError, IndexError, TypeError):
        pass
    try:
        return 1 if len(series) else 0
    except TypeError:
        return 0


def stream_origin_id(source_key: str, path: Path, stream_index: int) -> str:
    """Origin id the native merger must persist on its corresponding stream."""

    if not source_key.strip() or stream_index < 0:
        raise ValueError("source_key and a non-negative stream_index are required")
    return f"{source_key}:{Path(path).name}:{stream_index}"


def _best_effort_sha256(path: Path) -> str | None:
    try:
        return sha256_file(path)
    except OSError:
        return None
