"""Turn a source's own times into the LSL clock of this computer.

Plugins and the marker outlet use these helpers instead of computing clock
offsets themselves. ``pylsl.local_clock`` is a steady, high-resolution clock;
``time.time`` and, on Windows, ``time.monotonic`` tick only every ~15.6 ms.
So a source timeline runs on ``time.perf_counter`` and every offset between
two clocks is measured once (tightly bracketed, or on a wall-clock tick edge)
and then held stable, instead of being re-read and re-rounded per sample.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Mapping, Sequence

from .contract import (
    CALLBACK_JITTER_TOLERANCE_SECONDS,
    CALLBACK_MAX_SLEW_FRACTION,
    WALL_CLOCK_STEP_TOLERANCE_SECONDS,
)

Clock = Callable[[], float]

SOURCE_COUNTER = "perf_counter"


def callback_batch_start(
    *,
    received_epoch: float,
    within_batch_steps: int,
    previous_timestamp: float | None,
    first_step: int,
    sample_interval: float,
    jitter_tolerance_seconds: float = CALLBACK_JITTER_TOLERANCE_SECONDS,
    max_slew_fraction: float = CALLBACK_MAX_SLEW_FRACTION,
) -> tuple[float, float, float | None]:
    """``(start, spacing, discontinuity)`` for one host-callback batch.

    The receipt time is the reference: the batch's last sample cannot have
    been measured after the callback delivered it. Within the jitter tolerance
    the timeline keeps its own smooth spacing, stretched or compressed by at
    most ``max_slew_fraction`` toward the receipt, so a device rate that
    differs slightly from the nominal one cannot accumulate into drift.
    Beyond the tolerance the timeline is re-anchored and the disagreement is
    returned for the plugin to report: a pause (receipt later than the
    timeline) jumps forward to the receipt, and a timeline ahead of the
    receipt (a packet-counter leap) is compressed so its last sample lands on
    the receipt. No sample is invented, and none is dated after its arrival
    by more than the tolerance.
    """

    receipt_start = float(received_epoch) - within_batch_steps * sample_interval
    if not all(math.isfinite(value) for value in (receipt_start, sample_interval)):
        raise ValueError("callback timestamps must be finite")
    if previous_timestamp is None:
        return receipt_start, sample_interval, None
    steps = max(1, first_step + within_batch_steps)
    predicted_start = previous_timestamp + first_step * sample_interval
    drift = receipt_start - predicted_start
    if drift > jitter_tolerance_seconds:
        return receipt_start, sample_interval, drift
    if drift < -jitter_tolerance_seconds:
        room = float(received_epoch) - previous_timestamp
        spacing = room / steps if room > 0 else sample_interval * (1.0 - max_slew_fraction)
        return previous_timestamp + first_step * spacing, spacing, drift
    slew = max(-max_slew_fraction, min(max_slew_fraction, drift / (steps * sample_interval)))
    spacing = sample_interval * (1.0 + slew)
    return previous_timestamp + first_step * spacing, spacing, None


def _bracketed_offset(target: Clock, reference: Clock, rounds: int = 7) -> float:
    """``target - reference`` from the tightest of a few bracketed readings."""

    best: tuple[float, float] | None = None
    for _ in range(rounds):
        before = reference()
        value = target()
        after = reference()
        width = after - before
        if best is None or width < best[0]:
            best = (width, value - (before + after) / 2.0)
    assert best is not None
    return best[1]


def _tick_edge_offset(target: Clock, coarse: Clock, limit_seconds: float = 0.05) -> float:
    """``target - coarse`` read right after ``coarse`` ticks, so its rounding drops out."""

    start = coarse()
    deadline = time.perf_counter() + limit_seconds
    while time.perf_counter() < deadline:
        value = target()
        current = coarse()
        if current != start:
            return value - current
    return target() - coarse()


class SourceClock:
    """A plugin's source timeline: epoch-like seconds on ``time.perf_counter``.

    ``now()`` reads like Unix time for logs and history files but advances
    with the high-resolution counter, so a wall-clock adjustment cannot move
    it. ``anchors()`` is what a source process reports once (the plugin's
    CLOCK line) so its host can map these times onto the LSL clock.
    """

    def __init__(self, *, counter: Clock | None = None, wall: Clock | None = None) -> None:
        self._counter = counter or time.perf_counter
        self.counter_anchor = self._counter()
        self.epoch_anchor = self.counter_anchor - _tick_edge_offset(self._counter, wall or time.time)

    def now(self) -> float:
        return self.epoch_anchor + (self._counter() - self.counter_anchor)

    def anchors(self) -> dict[str, Any]:
        return {
            "epoch_anchor": self.epoch_anchor,
            "counter_anchor": self.counter_anchor,
            "counter": SOURCE_COUNTER,
        }


class SourceToLsl:
    """Map times of a reported ``SourceClock`` onto this computer's LSL clock.

    ``perf_counter`` is system-wide, so the source process and this one read
    the same counter. The counter-to-LSL offset is measured once and held;
    both clocks are steady, so a later re-measurement could only add noise.
    """

    def __init__(self, anchors: Mapping[str, Any], *, lsl_clock: Clock, counter: Clock | None = None) -> None:
        if anchors.get("counter") != SOURCE_COUNTER:
            raise ValueError(f"unsupported source counter: {anchors.get('counter')!r}")
        self.epoch_anchor = float(anchors["epoch_anchor"])
        self.counter_anchor = float(anchors["counter_anchor"])
        if not (math.isfinite(self.epoch_anchor) and math.isfinite(self.counter_anchor)):
            raise ValueError("source clock anchors must be finite")
        self.counter_to_lsl = _bracketed_offset(lsl_clock, counter or time.perf_counter)

    def to_lsl(self, source_times: Sequence[float]) -> list[float]:
        shift = self.counter_anchor - self.epoch_anchor + self.counter_to_lsl
        return [float(value) + shift for value in source_times]


class WallToLsl:
    """Map server wall-clock instants onto the LSL clock, refusing after a step.

    The offset is read on a wall-clock tick edge, so the coarse ``time.time``
    resolution does not bias every mapped marker. ``check_step()`` reports and
    adopts a wall-clock step (NTP, manual change) larger than the tolerance;
    instants recorded before such a step must not be mapped with either offset.
    """

    def __init__(self, *, lsl_clock: Clock, wall: Clock | None = None) -> None:
        self._lsl_clock = lsl_clock
        self._wall = wall or time.time
        self.offset = _tick_edge_offset(lsl_clock, self._wall)

    def check_step(self) -> float | None:
        """The wall-clock step in milliseconds since the last check, or None."""
        try:
            current = float(self._lsl_clock()) - float(self._wall())
        except (TypeError, ValueError, RuntimeError):
            return None
        if not math.isfinite(current):
            return None
        difference = current - self.offset
        if abs(difference) <= WALL_CLOCK_STEP_TOLERANCE_SECONDS:
            return None
        self.offset = _tick_edge_offset(self._lsl_clock, self._wall)
        return round(difference * 1000.0, 3)

    def to_lsl(self, epoch_ms: Any) -> float | None:
        try:
            value = float(epoch_ms)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        mapped = value / 1000.0 + self.offset
        return mapped if math.isfinite(mapped) else None
