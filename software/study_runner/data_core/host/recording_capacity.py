"""Preflight: does the target disk actually have room for this session?

Package 5b (docs/archive/architecture-1.0-umbau.md). The target architecture's own
wording talks about checking free space against "die geplante Sessiondauer
und die gemessene Schreibrate" (the planned session duration and the
*measured write rate*). This module deliberately does not measure disk
write throughput: a benchmark would report the storage device's raw
sequential-write speed -- hundreds of MB/s on any SSD -- against an
acquisition stream producing kilobytes per second. That check would always
pass and prove nothing.

Instead, the required capacity is computed purely from what is already
known and declared: the negotiated recording contract's stream list (channel
count, sample format, and rate straight from each plugin's manifest) plus the
study's own planned duration. Disk throughput never enters the calculation.

Fail-closed by design: if the planned duration is not configured, this
reports `known=False`, never a silent "sufficient". A missing input is not
the same as a passing check.
"""
from __future__ import annotations

from pathlib import Path
import math
import shutil
from typing import Any, Mapping


# Bytes per sample for each LSL channel format (see contracts/manifest.py's
# _LSL_CHANNEL_FORMATS, the source of truth this mirrors). "string" channels
# have no fixed size on the wire; markers/events are the only channels that
# use it today, so a documented fixed estimate stands in rather than reading
# actual payloads before a session has even started.
BYTES_PER_SAMPLE: dict[str, int] = {
    "int8": 1,
    "int16": 2,
    "int32": 4,
    "int64": 8,
    "float32": 4,
    "double64": 8,
}
ESTIMATED_STRING_CHANNEL_BYTES = 64

# Operational policy, not something derivable from the machine: how much
# headroom to demand beyond the raw estimate (container overhead, an
# unplanned few extra minutes), and an absolute floor kept free regardless of
# how small the estimate is. Versioned so a session's recorded plan can note
# which policy generation it was checked against.
CAPACITY_SAFETY_FACTOR = 1.5
MINIMUM_FREE_SPACE_RESERVE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
PREFLIGHT_THRESHOLDS_VERSION = 1


def estimated_acquisition_bytes_per_second(
    streams_by_source: Mapping[str, list[Mapping[str, Any]]],
    backup_contract: Mapping[str, Any],
) -> float:
    """Sum declared stream rates from the negotiated recording contract.

    Never reads disk throughput or any live measurement -- every input here
    is either a manifest-declared rate or the backup grid rate computed at
    session-contract build time.
    """

    total = 0.0
    for streams in streams_by_source.values():
        for stream in streams:
            channels = stream.get("channels") if isinstance(stream, Mapping) else None
            channel_count = len(channels) if isinstance(channels, list) else 0
            channel_format = str((stream or {}).get("channel_format") or "")
            rate_hz = float((stream or {}).get("nominal_rate_hz") or 0.0)
            if not math.isfinite(rate_hz) or rate_hz < 0:
                raise ValueError("stream nominal_rate_hz must be a finite non-negative number")
            bytes_per_sample = BYTES_PER_SAMPLE.get(channel_format, ESTIMATED_STRING_CHANNEL_BYTES)
            total += channel_count * bytes_per_sample * rate_hz

    backup_channels = backup_contract.get("channel_names") if isinstance(backup_contract, Mapping) else None
    backup_channel_count = len(backup_channels) if isinstance(backup_channels, list) else 0
    backup_rate_hz = float((backup_contract or {}).get("rate_hz") or 0.0)
    if not math.isfinite(backup_rate_hz) or backup_rate_hz < 0:
        raise ValueError("backup rate_hz must be a finite non-negative number")
    total += backup_channel_count * BYTES_PER_SAMPLE["double64"] * backup_rate_hz
    return total


def planned_duration_seconds(config_data: Mapping[str, Any]) -> float | None:
    """The study's own declared planned duration, or None when unset.

    None means unknown, never zero -- a study that has not declared a
    duration has not promised anything about how much data it will produce.
    """

    settings = config_data.get("study_settings") if isinstance(config_data, Mapping) else None
    settings = settings if isinstance(settings, Mapping) else {}
    minutes = settings.get("planned_session_duration_minutes")
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, (int, float))
        or not math.isfinite(float(minutes))
        or minutes <= 0
    ):
        return None
    return float(minutes) * 60.0


def evaluate_capacity(
    *,
    target_dir: Path,
    streams_by_source: Mapping[str, list[Mapping[str, Any]]],
    backup_contract: Mapping[str, Any],
    config_data: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed when duration is unknown; otherwise compare against free space."""

    duration_seconds = planned_duration_seconds(config_data)
    if duration_seconds is None:
        return {
            "ok": False,
            "known": False,
            "thresholds_version": PREFLIGHT_THRESHOLDS_VERSION,
            "reason": (
                "planned_session_duration_minutes is not configured for this study; "
                "storage capacity cannot be predicted"
            ),
        }

    try:
        bytes_per_second = estimated_acquisition_bytes_per_second(streams_by_source, backup_contract)
    except (TypeError, ValueError, OverflowError) as error:
        return {
            "ok": False,
            "known": False,
            "thresholds_version": PREFLIGHT_THRESHOLDS_VERSION,
            "reason": f"recording stream rate is invalid; storage capacity cannot be predicted: {error}",
        }
    required_bytes = int(duration_seconds * bytes_per_second * CAPACITY_SAFETY_FACTOR) + MINIMUM_FREE_SPACE_RESERVE_BYTES
    try:
        free_bytes = shutil.disk_usage(Path(target_dir)).free
    except OSError as error:
        return {
            "ok": False,
            "known": False,
            "required_bytes": required_bytes,
            "planned_duration_seconds": duration_seconds,
            "estimated_bytes_per_second": bytes_per_second,
            "reserve_bytes": MINIMUM_FREE_SPACE_RESERVE_BYTES,
            "thresholds_version": PREFLIGHT_THRESHOLDS_VERSION,
            "reason": f"free storage could not be read for {Path(target_dir)}: {error}",
        }
    ok = free_bytes >= required_bytes
    return {
        "ok": ok,
        "known": True,
        "free_bytes": free_bytes,
        "required_bytes": required_bytes,
        "planned_duration_seconds": duration_seconds,
        "estimated_bytes_per_second": bytes_per_second,
        "reserve_bytes": MINIMUM_FREE_SPACE_RESERVE_BYTES,
        "thresholds_version": PREFLIGHT_THRESHOLDS_VERSION,
        "reason": (
            None
            if ok
            else (
                f"{free_bytes} bytes free, {required_bytes} bytes required "
                f"for a planned {duration_seconds / 60.0:.0f}-minute session"
            )
        ),
    }
