"""Compare recorded times, live and offline, by the same rules.

Live functions read one stream's state as the recording worker publishes it
(``last_timestamp`` on the outlet's clock, ``clock_correction_seconds`` to the
recorder's clock, ``last_receipt_lsl`` on the recorder's clock). Offline
functions read the XDF: raw timestamps, the recorded clock offsets, and the
pyxdf clock-synchronized view of the same samples.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .contract import (
    BASIS_CORRECTED,
    BASIS_RECEIPT,
    CORRECTED,
    DISCONTINUITY_PERIODS,
    LOCAL,
    UNCERTAIN,
)

CLOCK_REPORT_SCHEMA = "study-runner/clock-report/v1"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def regular_rate(item: Mapping[str, Any]) -> float:
    rate = _finite(item.get("nominal_rate_hz"))
    return rate if rate is not None and rate > 0 else 0.0


# ------------------------------------------------------------------ live


def stream_clock_status(item: Mapping[str, Any]) -> str:
    """``corrected`` only with a fresh, finite correction from the worker."""
    if item.get("clock_status") == CORRECTED and _finite(item.get("clock_correction_seconds")) is not None:
        return CORRECTED
    return UNCERTAIN


def recorder_time_of_last_sample(item: Mapping[str, Any]) -> float | None:
    """The last sample's timestamp on the recorder's clock, if it is known."""
    if stream_clock_status(item) != CORRECTED:
        return None
    last = _finite(item.get("last_timestamp"))
    if last is None:
        return None
    return last + float(item["clock_correction_seconds"])


def start_lag_seconds(item: Mapping[str, Any], recorder_now: Any) -> float | None:
    """How far the newest sample's timestamp is behind the recorder's now.

    None when the comparison is not possible (no correction yet); a stream
    whose timestamps run far behind its arrival then cannot pass as aligned.
    """
    now = _finite(recorder_now)
    last = recorder_time_of_last_sample(item)
    if now is None or last is None:
        return None
    return now - last


def marker_coverage(item: Mapping[str, Any], marker_recorder_time: float) -> tuple[bool, str]:
    """Whether a stream reached the end marker, and on which basis.

    With a correction, the last sample's own timestamp must reach the marker.
    Without one, the only comparable reading is the recorder's receipt time,
    which shows that data arrived after the marker but not when it was
    measured; the basis says so.
    """
    corrected = recorder_time_of_last_sample(item)
    if corrected is not None:
        return corrected >= marker_recorder_time, BASIS_CORRECTED
    receipt = _finite(item.get("last_receipt_lsl"))
    return receipt is not None and receipt >= marker_recorder_time, BASIS_RECEIPT


def uncertain_regular_streams(
    source_states: Mapping[str, Any],
    source_keys: Iterable[str],
) -> list[str]:
    """``plugin.stream`` for every regular stream without a usable correction."""
    names: list[str] = []
    for plugin_key in sorted(source_keys):
        source = source_states.get(plugin_key)
        if not isinstance(source, Mapping):
            continue
        for item in source.get("streams") or []:
            if isinstance(item, Mapping) and regular_rate(item) > 0 and stream_clock_status(item) != CORRECTED:
                names.append(f"{plugin_key}.{item.get('key') or item.get('source_id') or 'stream'}")
    return names


# --------------------------------------------------------------- offline


def alignment_status(*, recorder_local: bool, clock_offset_count: int, synchronized_available: bool) -> str:
    if recorder_local:
        return LOCAL
    if clock_offset_count > 0 and synchronized_available:
        return CORRECTED
    return UNCERTAIN


def window_sample_count(timestamps: Any, start: float, end: float) -> int:
    """Samples whose (recorder-clock) timestamp lies inside ``[start, end]``."""
    try:
        import numpy as np

        values = np.asarray(timestamps, dtype=float)
        return int(np.count_nonzero((values >= start) & (values <= end)))
    except ImportError:
        return sum(1 for value in timestamps if start <= float(value) <= end)


def timeline_discontinuities(timestamps: Sequence[float] | Any, nominal_rate_hz: float) -> dict[str, Any]:
    """Forward jumps longer than ``DISCONTINUITY_PERIODS`` nominal periods.

    Generic for every regular stream: a paused callback that was re-anchored,
    or a real transmission gap, both show up here without plugin knowledge.
    """
    if nominal_rate_hz <= 0:
        return {"count": 0, "largest_seconds": None}
    threshold = DISCONTINUITY_PERIODS / float(nominal_rate_hz)
    try:
        import numpy as np

        values = np.asarray(timestamps, dtype=float)
        if values.size < 2:
            return {"count": 0, "largest_seconds": None}
        gaps = np.diff(values)
        jumps = gaps[gaps > threshold]
        return {
            "count": int(jumps.size),
            "largest_seconds": round(float(jumps.max()), 6) if jumps.size else None,
        }
    except ImportError:
        values = [float(value) for value in timestamps]
        jumps = [right - left for left, right in zip(values, values[1:]) if right - left > threshold]
        return {"count": len(jumps), "largest_seconds": round(max(jumps), 6) if jumps else None}


def correction_summary(clock_values: Any) -> dict[str, Any]:
    """Statistics of recorded LSL corrections (seconds), never a merged time."""
    try:
        values = [float(value) for value in clock_values]
    except (TypeError, ValueError):
        values = []
    values = [value for value in values if math.isfinite(value)]
    if not values:
        return {"count": 0, "median_seconds": None, "min_seconds": None, "max_seconds": None}
    return {
        "count": len(values),
        "median_seconds": median(values),
        "min_seconds": min(values),
        "max_seconds": max(values),
    }


def display_epoch_offset(
    marker_lsl_times: Iterable[float],
    marker_server_epoch_ms: Iterable[float],
) -> float | None:
    """Seconds to add to a recorder-clock time to show it as Unix time.

    For display only and never stored: every study marker carries the server
    wall time it was pushed for (``server_ms``) and its LSL timestamp, so the
    median of their differences places the whole recording on the same axis
    as the participant's card times. None without a usable marker pair.
    """
    differences = []
    for lsl_time, server_ms in zip(marker_lsl_times, marker_server_epoch_ms):
        lsl_value, server_value = _finite(lsl_time), _finite(server_ms)
        if lsl_value is not None and server_value is not None and server_value > 0:
            differences.append(server_value / 1000.0 - lsl_value)
    return median(differences) if differences else None


def build_clock_report(
    *,
    session_id: str,
    declared_streams: Mapping[str, Sequence[Mapping[str, Any]]],
    stream_metrics: Sequence[Mapping[str, Any]],
    start_barrier: Mapping[str, Any] | None,
    end_barrier: Mapping[str, Any] | None,
    marker_window: Mapping[str, Any],
) -> dict[str, Any]:
    """The per-session audit record of every clock decision.

    Offline values come only from the XDF, so anyone can recompute them.
    Live barrier outcomes are copied as observed at the time.
    """
    sources = {
        (plugin_key, str(stream.get("source_id") or "")): stream
        for plugin_key, streams in declared_streams.items()
        for stream in streams
    }
    streams = []
    for metric in stream_metrics:
        key = (str(metric.get("source_key") or ""), str(metric.get("source_id") or ""))
        declared = sources.get(key) or {}
        streams.append(
            {
                "source_key": key[0],
                "source_id": key[1],
                "timestamp_source": (declared.get("timing") or {}).get("timestamp_source"),
                "nominal_rate_hz": metric.get("nominal_rate_hz"),
                "clock_alignment": metric.get("clock_alignment"),
                "clock_corrections": metric.get("clock_corrections"),
                "timeline_discontinuities": metric.get("timeline_discontinuities"),
                "window_sample_count": metric.get("window_sample_count"),
                "expected_sample_count": metric.get("expected_sample_count"),
            }
        )
    return {
        "schema": CLOCK_REPORT_SCHEMA,
        "session_id": session_id,
        "rules": {
            "comparison": "only readings on the recorder's LSL clock, directly or through a recorded correction",
            "uncertain": "no usable correction; reported for review, never counted as data loss",
            "persisted_times": "raw timestamps and corrections only; no merged time is stored",
        },
        "marker_window": dict(marker_window),
        "start_barrier": dict(start_barrier or {}),
        "end_barrier": dict(end_barrier or {}),
        "streams": streams,
    }
