"""Bounded operator wait times for the shared recording lifecycle."""

from __future__ import annotations

import math
from typing import Any, Mapping


DEFAULT_START_WAIT_SECONDS = 8.0
DEFAULT_END_TAIL_WAIT_SECONDS = 10.0
MIN_WAIT_SECONDS = 1.0
MAX_WAIT_SECONDS = 30.0


def recording_timing(settings: Mapping[str, Any] | None) -> dict[str, float]:
    """Validate machine settings; only waiting changes, never data quality."""

    source = settings if isinstance(settings, Mapping) else {}
    defaults = {
        "start_wait_seconds": DEFAULT_START_WAIT_SECONDS,
        "end_tail_wait_seconds": DEFAULT_END_TAIL_WAIT_SECONDS,
    }
    unknown = set(source) - set(defaults)
    if unknown:
        raise ValueError("Unknown recording timing setting: " + ", ".join(sorted(unknown)))
    result: dict[str, float] = {}
    for key, default in defaults.items():
        value = source.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"recording_timing.{key} must be a number of seconds")
        number = float(value)
        if not math.isfinite(number) or not MIN_WAIT_SECONDS <= number <= MAX_WAIT_SECONDS:
            raise ValueError(
                f"recording_timing.{key} must be between {MIN_WAIT_SECONDS:g} and {MAX_WAIT_SECONDS:g} seconds"
            )
        result[key] = number
    return result
