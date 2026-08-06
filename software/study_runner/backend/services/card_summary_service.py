"""Deterministic card statistics derived from a validated merged XDF.

The builder deliberately knows nothing about Flask, live sensor buffers, or a
specific XDF library.  A small ``SampleReader`` contract keeps the statistics
testable with synthetic streams while ``PyXdfSampleReader`` is the production
importer.  XDF timestamps are read without clock synchronization or
dejittering; final merge validation is responsible for proving their parity
with the native source recordings.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import datetime as dt
import math
from pathlib import Path
import statistics
from typing import Any, Iterable, Protocol, Sequence


CARD_SUMMARY_SCHEMA = "study-runner/card-summary/v1"


class CardSummaryError(RuntimeError):
    """A merged XDF cannot be converted into trustworthy card statistics."""


class SampleReader(Protocol):
    """Read normalized streams from an XDF-like artifact.

    Every stream is a mapping with ``timestamps`` and ``samples`` of equal
    length. Samples may be channel dictionaries or row sequences accompanied
    by ``channels`` metadata.  This deliberately compact contract also makes
    fixture readers possible without generating binary XDF files.
    """

    def read_streams(self, path: Path) -> Iterable[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class CardWindow:
    card_id: str
    question_index: int | None
    question_type: str
    start_epoch: float
    end_epoch: float
    time_source: str

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_epoch - self.start_epoch)


class PyXdfSampleReader:
    """Production reader backed by pyxdf, imported lazily for packaging."""

    def read_streams(self, path: Path) -> list[dict[str, Any]]:
        try:
            import pyxdf  # type: ignore
        except ImportError as error:  # pragma: no cover - packaging guard
            raise CardSummaryError("pyxdf is required to build card summaries.") from error

        try:
            streams, _header = pyxdf.load_xdf(
                str(path),
                synchronize_clocks=False,
                dejitter_timestamps=False,
                handle_clock_resets=False,
                verbose=False,
            )
        except TypeError:
            # Older compatible pyxdf releases do not expose every keyword.
            streams, _header = pyxdf.load_xdf(
                str(path),
                synchronize_clocks=False,
                dejitter_timestamps=False,
                verbose=False,
            )
        except Exception as error:
            raise CardSummaryError(f"Could not read merged XDF: {error}") from error

        return [_normalize_pyxdf_stream(stream, index) for index, stream in enumerate(streams)]


class CardSummaryBuilder:
    """Compute descriptive statistics for half-open card windows ``[a, b)``."""

    def __init__(self, sample_reader: SampleReader | None = None) -> None:
        self.sample_reader = sample_reader or PyXdfSampleReader()

    def build(
        self,
        merged_xdf: Path,
        card_events: Sequence[dict[str, Any]],
        *,
        session_id: str = "",
        client_clock_offset_ms: Any = None,
        require_xdf_markers: bool = False,
        required_marker_event_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        merged_path = Path(merged_xdf)
        if not merged_path.is_file():
            raise CardSummaryError(f"Validated merged XDF is missing: {merged_path}")

        streams = list(self.sample_reader.read_streams(merged_path))
        source_streams = [_normalize_stream(stream, index) for index, stream in enumerate(streams)]
        normalized_streams = _coalesce_logical_streams(source_streams)
        marker_times, duplicate_marker_ids = _marker_event_times(normalized_streams)
        required_ids = {
            str(event_id).strip()
            for event_id in required_marker_event_ids
            if str(event_id).strip()
        }
        if require_xdf_markers and not marker_times:
            raise CardSummaryError(
                "Recording finalization requires XDF marker timestamps; no durable marker events were found."
            )
        missing_required = sorted(required_ids - marker_times.keys())
        if missing_required:
            raise CardSummaryError(
                "Merged XDF is missing required terminal marker events: "
                + ", ".join(missing_required)
            )
        windows = _card_windows(
            card_events,
            client_clock_offset_ms=client_clock_offset_ms,
            marker_times=marker_times,
            require_markers=require_xdf_markers,
        )

        cards = []
        for window in windows:
            cards.append(
                {
                    "card_id": window.card_id,
                    "question_index": window.question_index,
                    "question_type": window.question_type,
                    "start_epoch": window.start_epoch,
                    "end_epoch": window.end_epoch,
                    "duration_seconds": window.duration_seconds,
                    "time_source": window.time_source,
                    "streams": {
                        stream["stream_key"]: _summarize_stream(stream, window)
                        for stream in normalized_streams
                    },
                }
            )

        result = {
            "schema": CARD_SUMMARY_SCHEMA,
            "session_id": str(session_id or ""),
            "source": {
                "artifact": merged_path.name,
                "reader": type(self.sample_reader).__name__,
                "clock_synchronization_applied": False,
                "dejittering_applied": False,
            },
            "window_semantics": "half_open_[start,end)",
            "card_count": len(cards),
            "stream_count": len(normalized_streams),
            "source_stream_count": len(source_streams),
            "cards": cards,
        }
        if duplicate_marker_ids:
            result["quality_warnings"] = [
                {
                    "code": "duplicate_marker_event_id",
                    "event_id": event_id,
                    "message": "The marker event id occurs more than once; the first raw timestamp defined the card window.",
                }
                for event_id in sorted(duplicate_marker_ids)
            ]
        return result


def _normalize_pyxdf_stream(stream: dict[str, Any], index: int) -> dict[str, Any]:
    info = stream.get("info") if isinstance(stream.get("info"), dict) else {}
    channel_descriptors = _pyxdf_channel_descriptors(info)
    labels = []
    channel_types: dict[str, str] = {}
    channel_units: dict[str, str] = {}
    for channel_index, descriptor in enumerate(channel_descriptors):
        label = _first_scalar(descriptor.get("label")) or f"channel_{channel_index + 1}"
        labels.append(str(label))
        declared_type = _first_scalar(descriptor.get("type"))
        if declared_type:
            channel_types[str(label)] = str(declared_type)
        declared_unit = _first_scalar(descriptor.get("unit"))
        if declared_unit:
            channel_units[str(label)] = str(declared_unit)

    series = _plain_sequence(stream.get("time_series"))
    if series and not labels:
        first_row = _plain_sequence(series[0])
        width = len(first_row) if first_row else 1
        labels = [f"channel_{offset + 1}" for offset in range(width)]

    samples = []
    for row in series:
        values = _plain_sequence(row)
        if not values:
            values = [row]
        samples.append({label: values[offset] if offset < len(values) else None for offset, label in enumerate(labels)})

    return {
        "stream_key": str(_first_scalar(info.get("stream_id")) or _first_scalar(info.get("source_id")) or f"stream-{index + 1}"),
        "stream_id": str(_first_scalar(info.get("stream_id")) or ""),
        "source_id": str(_first_scalar(info.get("source_id")) or ""),
        "plugin_key": str(_study_runner_metadata(info, "plugin_key") or ""),
        "name": str(_first_scalar(info.get("name")) or f"Stream {index + 1}"),
        "nominal_rate_hz": _finite_number(_first_scalar(info.get("nominal_srate"))) or 0.0,
        "channels": labels,
        "channel_types": channel_types,
        "channel_units": channel_units,
        "timestamps": _plain_sequence(stream.get("time_stamps")),
        "samples": samples,
        "metadata": info,
    }


def _normalize_stream(stream: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(stream, dict):
        raise CardSummaryError(f"Sample reader returned invalid stream {index + 1}.")
    timestamps = [_finite_number(value) for value in _plain_sequence(stream.get("timestamps"))]
    samples = _plain_sequence(stream.get("samples"))
    if len(timestamps) != len(samples) or any(value is None for value in timestamps):
        raise CardSummaryError(f"Stream {index + 1} has mismatched or invalid timestamps.")

    channels = stream.get("channels") if isinstance(stream.get("channels"), list) else []
    labels = [
        str(item.get("label") if isinstance(item, dict) else item)
        for item in channels
    ]
    channel_types = dict(stream.get("channel_types") or {})
    normalized_samples = []
    for row in samples:
        if isinstance(row, dict):
            normalized_samples.append(dict(row))
            continue
        values = _plain_sequence(row)
        if not labels:
            labels = [f"channel_{offset + 1}" for offset in range(len(values) or 1)]
        normalized_samples.append(
            {label: values[offset] if offset < len(values) else None for offset, label in enumerate(labels)}
        )

    key = str(
        stream.get("stream_key")
        or stream.get("stream_id")
        or stream.get("source_id")
        or stream.get("name")
        or f"stream-{index + 1}"
    )
    return {
        **stream,
        "stream_key": key,
        "stream_id": str(stream.get("stream_id") or ""),
        "source_id": str(stream.get("source_id") or ""),
        "plugin_key": str(stream.get("plugin_key") or ""),
        "name": str(stream.get("name") or key),
        "nominal_rate_hz": _finite_number(stream.get("nominal_rate_hz")) or 0.0,
        "channel_types": channel_types,
        "timestamps": [float(value) for value in timestamps if value is not None],
        "samples": normalized_samples,
    }


def _coalesce_logical_streams(streams: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join recovery segments before computing a card statistic.

    The XDF merge deliberately keeps every source segment as its own container
    stream.  A worker restart can therefore produce multiple stream IDs for one
    physical signal.  Card statistics, however, describe the logical signal,
    not the files that happened to contain it.  Only the manifest plugin key
    plus stable LSL source ID is strong enough to join segments.  Streams that
    lack either identifier stay independent.

    Structural metadata must remain identical across all joined segments.  A
    mismatch is a quality failure rather than something that may be silently
    averaged together.
    """

    groups: list[list[dict[str, Any]]] = []
    group_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for stream in streams:
        plugin_key = str(stream.get("plugin_key") or "").strip()
        source_id = str(stream.get("source_id") or "").strip()
        if not plugin_key or not source_id:
            groups.append([stream])
            continue
        identity = (plugin_key, source_id)
        group = group_by_identity.get(identity)
        if group is None:
            group = []
            group_by_identity[identity] = group
            groups.append(group)
        group.append(stream)

    coalesced = [_coalesce_stream_group(group) for group in groups]
    _make_stream_keys_unique(coalesced)
    return coalesced


def _coalesce_stream_group(group: Sequence[dict[str, Any]]) -> dict[str, Any]:
    first = dict(group[0])
    reference_signature = _logical_stream_signature(first)
    for segment in group[1:]:
        if _logical_stream_signature(segment) != reference_signature:
            plugin_key = first.get("plugin_key") or "unknown-plugin"
            source_id = first.get("source_id") or "unknown-source"
            raise CardSummaryError(
                "Recovery segments disagree on logical stream metadata for "
                f"{plugin_key}/{source_id}."
            )

    ordered_samples: list[tuple[float, int, int, dict[str, Any]]] = []
    segment_stream_ids: list[str] = []
    for segment_index, segment in enumerate(group):
        stream_id = str(segment.get("stream_id") or "").strip()
        if stream_id and stream_id not in segment_stream_ids:
            segment_stream_ids.append(stream_id)
        for sample_index, (timestamp, sample) in enumerate(
            zip(segment.get("timestamps") or [], segment.get("samples") or [])
        ):
            ordered_samples.append((float(timestamp), segment_index, sample_index, dict(sample)))
    ordered_samples.sort(key=lambda item: (item[0], item[1], item[2]))

    first["timestamps"] = [item[0] for item in ordered_samples]
    first["samples"] = [item[3] for item in ordered_samples]
    first["segment_count"] = len(group)
    first["segment_stream_ids"] = segment_stream_ids
    return first


def _logical_stream_signature(stream: dict[str, Any]) -> tuple[Any, ...]:
    labels: list[str] = []
    for channel in stream.get("channels") or []:
        label = channel.get("label") if isinstance(channel, dict) else channel
        text = str(label or "")
        if text and text not in labels:
            labels.append(text)
    for sample in stream.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        for label in sample:
            text = str(label)
            if text not in labels:
                labels.append(text)
    declared_types = tuple(
        sorted((str(key), str(value)) for key, value in (stream.get("channel_types") or {}).items())
    )
    return (
        str(stream.get("plugin_key") or ""),
        str(stream.get("source_id") or ""),
        str(stream.get("name") or ""),
        float(stream.get("nominal_rate_hz") or 0.0),
        tuple(labels),
        declared_types,
    )


def _make_stream_keys_unique(streams: Sequence[dict[str, Any]]) -> None:
    used: set[str] = set()
    for index, stream in enumerate(streams, start=1):
        base = str(stream.get("stream_key") or stream.get("source_id") or f"stream-{index}")
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}__{suffix}"
            suffix += 1
        stream["stream_key"] = candidate
        used.add(candidate)


def _summarize_stream(stream: dict[str, Any], window: CardWindow) -> dict[str, Any]:
    selected = [
        (timestamp, sample)
        for timestamp, sample in zip(stream["timestamps"], stream["samples"])
        if window.start_epoch <= timestamp < window.end_epoch
    ]
    timestamps = [item[0] for item in selected]
    samples = [item[1] for item in selected]
    rate_hz = float(stream.get("nominal_rate_hz") or 0.0)
    expected_count = max(0, round(window.duration_seconds * rate_hz)) if rate_hz > 0 else None
    missing_count = max(0, expected_count - len(samples)) if expected_count is not None else None
    coverage = (
        min(1.0, len(samples) / expected_count)
        if expected_count
        else (1.0 if expected_count == 0 and not samples else None)
    )

    labels: list[str] = []
    for sample in samples:
        for label in sample:
            if label not in labels:
                labels.append(label)
    declared_types = stream.get("channel_types") or {}
    valid_flags = [_sample_valid(sample) for sample in samples]
    channels = {
        label: _channel_statistics(
            [sample.get(label) if valid_flags[offset] else None for offset, sample in enumerate(samples)],
            declared_type=str(declared_types.get(label) or ""),
        )
        for label in labels
        if label.lower() not in {"valid", "status", "sequence"}
    }

    return {
        "plugin_key": stream.get("plugin_key") or None,
        "stream_id": stream.get("stream_id") or None,
        "source_id": stream.get("source_id") or None,
        "name": stream.get("name"),
        "segment_count": int(stream.get("segment_count") or 1),
        "segment_stream_ids": list(stream.get("segment_stream_ids") or []),
        "nominal_rate_hz": rate_hz,
        "count": len(samples),
        "valid_count": sum(valid_flags),
        "expected_count": expected_count,
        "coverage": coverage,
        "missing_count": missing_count,
        "drop_count": _sequence_drops(samples),
        "max_gap_seconds": _max_gap(timestamps),
        "first_timestamp": timestamps[0] if timestamps else None,
        "last_timestamp": timestamps[-1] if timestamps else None,
        "time_source": "xdf_raw_timestamps",
        "plugin_status": _mode([sample.get("status") for sample in samples if sample.get("status") is not None]),
        "channels": channels,
    }


def _channel_statistics(values: list[Any], *, declared_type: str) -> dict[str, Any]:
    valid_values = [value for value in values if _is_valid_scalar(value)]
    bool_channel = declared_type.strip().lower() in {"bool", "boolean"} or (
        bool(valid_values) and all(isinstance(value, bool) for value in valid_values)
    )
    numeric_values: list[float] = []
    if bool_channel:
        numeric_values = [1.0 if bool(value) else 0.0 for value in valid_values]
    elif all(_finite_number(value) is not None for value in valid_values):
        numeric_values = [float(_finite_number(value)) for value in valid_values]  # type: ignore[arg-type]

    common = {"count": len(values), "valid_count": len(valid_values)}
    if numeric_values or (not valid_values and declared_type.strip().lower() not in {"string", "categorical"}):
        return {
            **common,
            "kind": "boolean" if bool_channel else "numeric",
            "mean": statistics.fmean(numeric_values) if numeric_values else None,
            "min": min(numeric_values) if numeric_values else None,
            "max": max(numeric_values) if numeric_values else None,
            "stddev": statistics.stdev(numeric_values) if len(numeric_values) >= 2 else None,
        }

    labels = [str(value) for value in valid_values]
    frequencies = Counter(labels)
    return {
        **common,
        "kind": "categorical",
        "frequencies": dict(sorted(frequencies.items())),
        "mode": _mode(labels),
    }


def _card_windows(
    events: Sequence[dict[str, Any]],
    *,
    client_clock_offset_ms: Any,
    marker_times: dict[str, float] | None = None,
    require_markers: bool = False,
) -> list[CardWindow]:
    windows = []
    markers = marker_times or {}
    for index, event in enumerate(events or []):
        if not isinstance(event, dict):
            continue
        start_event_id = str(event.get("start_event_id") or event.get("shown_event_id") or "")
        end_event_id = str(event.get("stop_event_id") or event.get("answered_event_id") or "")
        if start_event_id and end_event_id and start_event_id in markers and end_event_id in markers:
            resolved = (
                markers[start_event_id],
                markers[end_event_id],
                "xdf_marker_event_ids",
            )
        elif (markers or require_markers) and start_event_id and end_event_id:
            missing = [event_id for event_id in (start_event_id, end_event_id) if event_id not in markers]
            raise CardSummaryError(
                f"Card event {index} is missing XDF marker timestamps for: {', '.join(missing)}."
            )
        elif (markers or require_markers) and not (start_event_id and end_event_id):
            # A pre-study card (normally Participant ID) has no sensor window.
            continue
        else:
            resolved = _event_epochs(event, client_clock_offset_ms)
        if resolved is None:
            continue
        start_epoch, end_epoch, source = resolved
        if end_epoch < start_epoch:
            raise CardSummaryError(f"Card event {index} ends before it starts.")
        question_index = _integer_or_none(event.get("question_index"))
        card_id = str(event.get("event_id") or event.get("card_id") or f"card-{question_index if question_index is not None else index}")
        windows.append(
            CardWindow(
                card_id=card_id,
                question_index=question_index,
                question_type=str(event.get("question_type") or event.get("type") or ""),
                start_epoch=start_epoch,
                end_epoch=end_epoch,
                time_source=source,
            )
        )
    return windows


def _marker_event_times(
    streams: Sequence[dict[str, Any]],
) -> tuple[dict[str, float], set[str]]:
    """Map durable event ids to their untouched marker-stream timestamps."""

    events: dict[str, float] = {}
    duplicates: set[str] = set()
    for stream in streams:
        for timestamp, sample in zip(stream.get("timestamps") or [], stream.get("samples") or []):
            if not isinstance(sample, dict):
                continue
            for value in sample.values():
                if not isinstance(value, str) or "event_id=" not in value:
                    continue
                fields = {}
                for item in value.split("|"):
                    key, separator, field_value = item.partition("=")
                    if separator:
                        fields[key.strip()] = field_value.strip()
                event_id = fields.get("event_id")
                if event_id:
                    if event_id in events:
                        duplicates.add(event_id)
                    else:
                        events[event_id] = float(timestamp)
    return events, duplicates


def _event_epochs(event: dict[str, Any], offset_ms: Any) -> tuple[float, float, str] | None:
    numeric_pairs = (
        ("client_start_trigger_epoch_ms", "client_stop_trigger_epoch_ms"),
        ("server_start_received_epoch_ms", "server_stop_received_epoch_ms"),
        ("shown_at_server_epoch_ms", "answered_at_server_epoch_ms"),
    )
    for start_key, end_key in numeric_pairs:
        start = _finite_number(event.get(start_key))
        end = _finite_number(event.get(end_key))
        if start is not None and end is not None:
            return start / 1000.0, end / 1000.0, start_key.rsplit("_epoch_ms", 1)[0]

    iso_pairs = (
        ("active_started_at", "active_ended_at"),
        ("shown_at", "answered_at"),
        ("shown_at", "completed_at"),
    )
    for start_key, end_key in iso_pairs:
        start = _iso_epoch(event.get(start_key))
        end = _iso_epoch(event.get(end_key))
        if start is None or end is None:
            continue
        offset = _finite_number(offset_ms)
        if offset is not None:
            return start + offset / 1000.0, end + offset / 1000.0, "client_iso_plus_clock_offset"
        return start, end, "client_iso"
    return None


def _sample_valid(sample: dict[str, Any]) -> bool:
    value = sample.get("valid")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "invalid", "missing"}
    return bool(value)


def _sequence_drops(samples: list[dict[str, Any]]) -> int | None:
    sequence_key = next(
        (key for key in ("sequence", "sequence_number", "seq") if any(key in sample for sample in samples)),
        None,
    )
    if sequence_key is None:
        return None
    values = [_integer_or_none(sample.get(sequence_key)) for sample in samples]
    sequence = [value for value in values if value is not None]
    return sum(max(0, current - previous - 1) for previous, current in zip(sequence, sequence[1:]))


def _max_gap(timestamps: list[float]) -> float | None:
    if len(timestamps) < 2:
        return None
    return max(max(0.0, current - previous) for previous, current in zip(timestamps, timestamps[1:]))


def _mode(values: list[Any]) -> Any:
    if not values:
        return None
    counts = Counter(values)
    highest = max(counts.values())
    return next(value for value in values if counts[value] == highest)


def _is_valid_scalar(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return isinstance(value, (str, bool, int, float))


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_epoch(value: Any) -> float | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _plain_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _first_scalar(value: Any) -> Any:
    current = value
    while isinstance(current, list) and current:
        current = current[0]
    return current


def _pyxdf_channel_descriptors(info: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        channels = info["desc"][0]["channels"][0]["channel"]
    except (KeyError, IndexError, TypeError):
        return []
    return [item for item in channels if isinstance(item, dict)]


def _study_runner_metadata(info: dict[str, Any], key: str) -> Any:
    flat_value = info.get(f"study_runner_{key}")
    if flat_value not in (None, "", []):
        return _first_scalar(flat_value)
    try:
        metadata = info["desc"][0]["study_runner"][0]
    except (KeyError, IndexError, TypeError):
        return None
    return _first_scalar(metadata.get(key)) if isinstance(metadata, dict) else None
