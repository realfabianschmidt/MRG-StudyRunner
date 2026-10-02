"""The sensor data contract every sensor plugin follows.

One sample path, three outputs (docs/plugin-recording-architecture.md,
"Sensor data contract"):

1. Raw: every real device sample becomes exactly one LSL sample on a
   manifest-declared stream, with an explicit timestamp. The recording
   worker writes it to XDF unchanged.
2. Backup: the worker samples the latest value of the declared
   ``backup_projection`` channels on a fixed grid. The rate is fixed here
   (1 Hz) for every sensor, not chosen per plugin.
3. Live: a dashboard-only view of declared ``live_view`` channels, the mean
   of every 0.5 s over the last 60 s. It never feeds anything recorded.

Every stream says where its timestamps come from (``timing.timestamp_source``).
A corrected timestamp is always reversible: the stream declares a
``correction_channel`` that holds, per sample, how many milliseconds were
taken off the arrival time (arrival = timestamp + correction_ms / 1000).

Pure standard library, like ``manifest.py`` (which delegates to this module
and turns ``SensorContractError`` into ``PluginManifestError``).
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

BACKUP_PROJECTION_RATE_HZ = 1.0
DEFAULT_BACKUP_STALE_AFTER_MS = 2500
LIVE_VIEW_RATE_HZ = 2
LIVE_VIEW_WINDOW_S = 60
LIVE_VIEW_POINTS = LIVE_VIEW_RATE_HZ * LIVE_VIEW_WINDOW_S
LIVE_VIEW_BUCKET_S = 1.0 / LIVE_VIEW_RATE_HZ
LIVE_VIEW_MAX_SERIES = 4
LIVE_VIEW_MAX_CHANNELS = 6
LIVE_VIEW_AXES = ("zero", "signed")

# host_arrival: the computer's LSL clock when the sample arrived here.
# host_arrival_corrected: the arrival time minus a measured delay, kept
#   reversible in the stream's correction channel.
# host_callback_reconstructed: a timeline rebuilt from the device driver's
#   callbacks (sample index x nominal period), as BrainBit's SDK client does.
TIMESTAMP_SOURCES = ("host_arrival", "host_arrival_corrected", "host_callback_reconstructed")
CORRECTION_UNIT = "millisecond"

CAPTURE_DELAY_SOURCES = ("measured", "datasheet", "estimated", "unknown")
UNKNOWN_CAPTURE_DELAY: dict[str, Any] = {
    "source": "unknown",
    "min_ns": None,
    "max_ns": None,
    "reference": None,
}
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class SensorContractError(ValueError):
    """A manifest violates the sensor data contract."""


# ------------------------------------------------------------------ timing

def normalize_stream_timing(
    value: Any,
    *,
    index: int,
    channels: list[str],
    channel_units: list[str],
) -> dict[str, Any]:
    """Timestamp source and capture-delay provenance for one stream.

    The capture delay defaults to an honest ``source: "unknown"`` rather
    than a fabricated number; anything else needs bounds and a reference
    for where the number came from. ``timestamp_source`` and
    ``correction_channel`` appear only when declared.
    """
    prefix = f"streams[{index}].timing"
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise SensorContractError(f"{prefix} must be a JSON object")
    unexpected = sorted(set(value) - {"capture_delay_ns", "timestamp_source", "correction_channel"})
    if unexpected:
        raise SensorContractError(f"{prefix} contains unsupported fields: " + ", ".join(unexpected))
    timing: dict[str, Any] = {"capture_delay_ns": _normalize_capture_delay(value.get("capture_delay_ns"), prefix)}

    source = _text(value.get("timestamp_source"))
    correction = _text(value.get("correction_channel"))
    if source:
        if source not in TIMESTAMP_SOURCES:
            raise SensorContractError(
                f"{prefix}.timestamp_source must be one of: " + ", ".join(TIMESTAMP_SOURCES)
            )
        timing["timestamp_source"] = source
    if source == "host_arrival_corrected" and not correction:
        raise SensorContractError(
            f"{prefix}.correction_channel is required for host_arrival_corrected: "
            "a corrected timestamp must stay reversible"
        )
    if correction:
        if source != "host_arrival_corrected":
            raise SensorContractError(f"{prefix}.correction_channel belongs to host_arrival_corrected")
        if correction not in channels:
            raise SensorContractError(f"{prefix}.correction_channel must name a declared channel")
        if channel_units[channels.index(correction)] != CORRECTION_UNIT:
            raise SensorContractError(f"{prefix}.correction_channel must have the unit {CORRECTION_UNIT}")
        timing["correction_channel"] = correction
    return timing


def _normalize_capture_delay(raw_delay: Any, prefix: str) -> dict[str, Any]:
    if raw_delay is None:
        return deepcopy(UNKNOWN_CAPTURE_DELAY)
    if not isinstance(raw_delay, dict):
        raise SensorContractError(f"{prefix}.capture_delay_ns must be a JSON object")
    unexpected = sorted(set(raw_delay) - {"source", "min_ns", "max_ns", "reference"})
    if unexpected:
        raise SensorContractError(
            f"{prefix}.capture_delay_ns contains unsupported fields: " + ", ".join(unexpected)
        )
    source = _text(raw_delay.get("source")) or "unknown"
    if source not in CAPTURE_DELAY_SOURCES:
        raise SensorContractError(
            f"{prefix}.capture_delay_ns.source must be one of: " + ", ".join(sorted(CAPTURE_DELAY_SOURCES))
        )
    if source == "unknown":
        return deepcopy(UNKNOWN_CAPTURE_DELAY)
    min_ns = _non_negative_int(raw_delay.get("min_ns"), f"{prefix}.capture_delay_ns.min_ns")
    max_ns = _non_negative_int(raw_delay.get("max_ns"), f"{prefix}.capture_delay_ns.max_ns")
    if max_ns < min_ns:
        raise SensorContractError(f"{prefix}.capture_delay_ns.max_ns must be >= min_ns")
    reference = _text(raw_delay.get("reference"))
    if not reference:
        raise SensorContractError(f"{prefix}.capture_delay_ns.reference is required")
    return {"source": source, "min_ns": min_ns, "max_ns": max_ns, "reference": reference}


# ------------------------------------------------------------------ backup

def normalize_backup_projection(projection: Any, streams: list[dict[str, Any]]) -> dict[str, Any]:
    """The backup projection with the core's fixed 1 Hz grid filled in."""
    if not isinstance(projection, dict):
        raise SensorContractError("backup_projection must be a JSON object")
    rate_hz = projection.get("rate_hz", BACKUP_PROJECTION_RATE_HZ)
    if isinstance(rate_hz, bool) or not isinstance(rate_hz, (int, float)) or rate_hz <= 0:
        raise SensorContractError("backup_projection.rate_hz must be positive")
    if float(rate_hz) != BACKUP_PROJECTION_RATE_HZ:
        raise SensorContractError(
            f"backup_projection.rate_hz is fixed at {BACKUP_PROJECTION_RATE_HZ:g} Hz for every sensor; "
            "leave it out"
        )
    stale_after_ms = projection.get("stale_after_ms", DEFAULT_BACKUP_STALE_AFTER_MS)
    if isinstance(stale_after_ms, bool) or not isinstance(stale_after_ms, (int, float)) or stale_after_ms <= 0:
        raise SensorContractError("backup_projection.stale_after_ms must be positive")
    raw_channels = projection.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise SensorContractError("backup_projection.channels must be a non-empty list")
    streams_by_key = {stream["key"]: stream for stream in streams}
    channels: list[dict[str, str]] = []
    outputs: set[str] = set()
    for index, raw_channel in enumerate(raw_channels, start=1):
        prefix = f"backup_projection.channels[{index}]"
        if not isinstance(raw_channel, dict):
            raise SensorContractError(f"{prefix} must be a JSON object")
        output = _text(raw_channel.get("output"))
        stream_id = _text(raw_channel.get("stream"))
        channel = _text(raw_channel.get("channel"))
        if not _KEY_PATTERN.fullmatch(output):
            raise SensorContractError(f"{prefix}.output must use lowercase snake_case")
        if not _KEY_PATTERN.fullmatch(stream_id):
            raise SensorContractError(f"{prefix}.stream must use lowercase snake_case")
        if not channel:
            raise SensorContractError(f"{prefix}.channel must be a non-empty string")
        if output in outputs:
            raise SensorContractError(f"duplicate backup projection output: {output}")
        outputs.add(output)
        source_stream = streams_by_key.get(stream_id)
        if source_stream is None:
            raise SensorContractError(f"backup projection stream is not declared: {stream_id}")
        if channel not in set(source_stream.get("channels") or []):
            raise SensorContractError(
                f"backup projection channel {channel!r} is absent from stream {stream_id!r}"
            )
        channels.append({"output": output, "stream": stream_id, "channel": channel})
    return {
        **projection,
        "rate_hz": BACKUP_PROJECTION_RATE_HZ,
        "stale_after_ms": stale_after_ms,
        "channels": channels,
    }


# -------------------------------------------------------------------- live

def normalize_live_view(config: Any, streams: list[dict[str, Any]]) -> dict[str, Any]:
    """Which recorded channels the dashboard shows live, and how.

    Rate and window are the core's (2 Hz, 60 s); a plugin only names the
    series. Each series draws 1-6 numeric channels of one declared stream.
    """
    if not isinstance(config, dict):
        raise SensorContractError("live_view must be a JSON object")
    unexpected = sorted(set(config) - {"series", "channel_key_prefix"})
    if unexpected:
        raise SensorContractError("live_view contains unsupported fields: " + ", ".join(unexpected))
    raw_series = config.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise SensorContractError("live_view.series must be a non-empty list")
    if len(raw_series) > LIVE_VIEW_MAX_SERIES:
        raise SensorContractError(f"live_view.series allows at most {LIVE_VIEW_MAX_SERIES} series")
    streams_by_key = {stream["key"]: stream for stream in streams}
    series: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_series, start=1):
        prefix = f"live_view.series[{index}]"
        if not isinstance(raw, dict):
            raise SensorContractError(f"{prefix} must be a JSON object")
        allowed = {"key", "stream", "channels", "label", "label_key", "help_key", "axis", "min_span", "scale", "unit_label"}
        unexpected = sorted(set(raw) - allowed)
        if unexpected:
            raise SensorContractError(f"{prefix} contains unsupported fields: " + ", ".join(unexpected))
        key = _text(raw.get("key"))
        if not _KEY_PATTERN.fullmatch(key):
            raise SensorContractError(f"{prefix}.key must be a snake_case key")
        if key in seen:
            raise SensorContractError(f"duplicate live_view series key: {key}")
        seen.add(key)
        stream = streams_by_key.get(_text(raw.get("stream")))
        if stream is None:
            raise SensorContractError(f"{prefix}.stream must name a declared stream")
        if stream.get("channel_format") == "string":
            raise SensorContractError(f"{prefix}.stream must be numeric")
        channels = raw.get("channels")
        if not isinstance(channels, list) or not 1 <= len(channels) <= LIVE_VIEW_MAX_CHANNELS:
            raise SensorContractError(f"{prefix}.channels must list 1 to {LIVE_VIEW_MAX_CHANNELS} channels")
        declared = list(stream.get("channels") or [])
        if any(not isinstance(name, str) or name not in declared for name in channels):
            raise SensorContractError(f"{prefix}.channels must be declared channels of stream {stream['key']!r}")
        if len(set(channels)) != len(channels):
            raise SensorContractError(f"{prefix}.channels must be unique")
        axis = _text(raw.get("axis")) or "zero"
        if axis not in LIVE_VIEW_AXES:
            raise SensorContractError(f"{prefix}.axis must be one of: " + ", ".join(LIVE_VIEW_AXES))
        min_span = _positive_number(raw.get("min_span", 1), f"{prefix}.min_span")
        scale = _positive_number(raw.get("scale", 1), f"{prefix}.scale")
        normalized = {
            "key": key,
            "stream": stream["key"],
            "channels": list(channels),
            "axis": axis,
            "min_span": min_span,
            "scale": scale,
            "unit_label": raw.get("unit_label") if isinstance(raw.get("unit_label"), str) else "",
        }
        for name in ("label", "label_key", "help_key"):
            text = _text(raw.get(name))
            if text:
                normalized[name] = text
        series.append(normalized)
    result: dict[str, Any] = {
        "rate_hz": LIVE_VIEW_RATE_HZ,
        "window_s": LIVE_VIEW_WINDOW_S,
        "series": series,
    }
    prefix_key = _text(config.get("channel_key_prefix"))
    if prefix_key:
        result["channel_key_prefix"] = prefix_key
    return result


# -------------------------------------------------------------- contract

def validate_sensor_contract(capabilities: dict[str, dict[str, Any]], streams: list[dict[str, Any]]) -> None:
    """What every recording sensor must declare (manifest-level, after normalization).

    A study sensor that records shows a live view and says, for every
    stream, where its timestamps come from.
    """
    if "study_sensor" not in capabilities or "recording_source" not in capabilities:
        return
    if "live_view" not in capabilities:
        raise SensorContractError("a recording study sensor must declare live_view (sensor data contract)")
    for stream in streams:
        timing = stream.get("timing") or {}
        if timing.get("timestamp_source") not in TIMESTAMP_SOURCES:
            raise SensorContractError(
                f"stream {stream['key']!r} must declare timing.timestamp_source (sensor data contract)"
            )
        if timing.get("timestamp_source") == "host_arrival_corrected" and stream.get("channel_format") == "string":
            raise SensorContractError(
                f"stream {stream['key']!r}: a corrected timestamp needs a numeric correction channel"
            )


def live_view_series(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """The normalized live-view series of a manifest (empty when none)."""
    live_view = (manifest.get("capability_config") or {}).get("live_view") or {}
    return list(live_view.get("series") or [])


# ----------------------------------------------------------------- helpers

def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise SensorContractError(f"{name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise SensorContractError(f"{name} must be a non-negative integer") from error
    if result < 0:
        raise SensorContractError(f"{name} must be a non-negative integer")
    return result


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
        raise SensorContractError(f"{name} must be a positive number")
    return float(value)
