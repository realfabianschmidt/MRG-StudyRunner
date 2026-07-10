"""Shared ring-buffer helpers for sensor sample history.

Every sensor adapter keeps its recent samples in a bounded deque so a
runaway session cannot exhaust memory. The buffers used to be fixed at
4096 samples, which silently dropped everything older than ~7 minutes
at 10 Hz. Now they are sized for a whole study session (default 2 h,
override with STUDY_RUNNER_SENSOR_BUFFER_SECONDS), and slicing can
report when a requested window predates the oldest retained sample.

Memory guide: one sample dict is roughly 0.5 kB, so a 10 Hz sensor over
2 hours keeps ~72 000 samples = ~35 MB.
"""
from __future__ import annotations

import os
from typing import Any

DEFAULT_MAX_SESSION_SECONDS = 2 * 60 * 60
BUFFER_SECONDS_ENV_VAR = "STUDY_RUNNER_SENSOR_BUFFER_SECONDS"


def history_maxlen(samples_per_second: float) -> int:
    """Return a deque maxlen that fits a full study session."""
    raw_value = os.environ.get(BUFFER_SECONDS_ENV_VAR, "").strip()
    try:
        seconds = float(raw_value) if raw_value else DEFAULT_MAX_SESSION_SECONDS
    except ValueError:
        seconds = DEFAULT_MAX_SESSION_SECONDS
    if seconds <= 0:
        seconds = DEFAULT_MAX_SESSION_SECONDS
    return max(4096, int(samples_per_second * seconds))


def samples_in_interval(
    history: Any,
    start_epoch: float,
    end_epoch: float,
    epoch_key: str = "_epoch",
) -> list[dict[str, Any]]:
    return [
        sample for sample in list(history)
        if start_epoch <= float(sample.get(epoch_key, 0.0)) <= end_epoch
    ]


def max_gap_seconds(samples: list[dict[str, Any]], epoch_key: str = "_epoch") -> float | None:
    """Largest pause between consecutive samples, for dropout detection."""
    if len(samples) < 2:
        return None
    epochs = sorted(float(sample.get(epoch_key, 0.0)) for sample in samples)
    return round(max(later - earlier for earlier, later in zip(epochs, epochs[1:])), 3)


def truncation_info(history: Any, start_epoch: float, epoch_key: str = "_epoch") -> dict[str, Any]:
    """Flag windows that reach back beyond the oldest retained sample.

    Only meaningful when the buffer is actually full: a partially filled
    buffer still holds everything that was ever recorded.
    """
    snapshot = list(history)
    buffer_full = getattr(history, "maxlen", None) is not None and len(snapshot) >= history.maxlen
    earliest_epoch = float(snapshot[0].get(epoch_key, 0.0)) if snapshot else None
    overflowed = bool(buffer_full and earliest_epoch is not None and start_epoch < earliest_epoch)
    info: dict[str, Any] = {"buffer_overflowed": overflowed}
    if overflowed:
        info["earliest_retained_epoch"] = earliest_epoch
    return info
