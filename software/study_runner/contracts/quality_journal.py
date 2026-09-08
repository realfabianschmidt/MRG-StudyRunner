"""Quality and timing observations made *while* recording, not afterwards.

Package 5c (docs/architecture-1.0-umbau.md), target doc §9: "Qualität
entsteht **während** der Aufnahme in ``quality.jsonl``, nicht erst bei der
Finalisierung. Eine abgebrochene Session hinterlässt sonst kein QC."

That sentence is the whole reason this module exists. The existing
``data_core/host/recording_quality.py`` is post-hoc by construction: it
consumes fully parsed XDF artifacts and validation reports, so a session
that never reaches finalization leaves nothing behind. What the ingest loop
can observe live is a different data model, computed from what actually
flows past it: source timestamps, receive times, and clock corrections.

Pure and dependency-free on purpose, so the detached worker process can use
it without importing anything host-side (invariant #2). The worker owns the
writing (``data_core/worker/session_journals.py``); this module only decides
*what counts as an observation* and *when a number becomes an event*.

Deliberately **not** covered here, with reasons rather than silence:

- **Queue utilisation / drops under saturation** belong to 5h, which
  introduces the bounded per-stream queues in the first place. Reporting on
  a queue that does not exist yet would be inventing a number.
- **Free storage during recording**: 5b already refuses to start without a
  plausible reserve. Continuous monitoring is a separate concern from
  ingest observation and is not smuggled in here.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

QUALITY_JOURNAL_SCHEMA = "study-runner/quality-journal/v1"
TIMING_JOURNAL_SCHEMA = "study-runner/timing-journal/v1"

# Target doc §9: "Schwellenwerte stehen in einem versionierten
# Qualitätsprofil." Versioned so a recording says which rules judged it --
# a later, stricter profile must not silently re-judge old sessions.
QUALITY_PROFILE_VERSION = 1
DEFAULT_QUALITY_PROFILE: dict[str, Any] = {
    "version": QUALITY_PROFILE_VERSION,
    # A gap is only meaningful against a declared rate. Three missed periods
    # is late enough to be a real transport stall rather than ordinary
    # scheduling noise on a busy machine.
    "gap_periods": 3.0,
    # Wall clock moving by more than this against the monotonic clock over
    # the same interval is an NTP correction or a manual change, not drift.
    "clock_jump_seconds": 1.0,
}

# Quality event names. `summary` is periodic and always emitted; the rest
# are only written when something actually happened, so an uneventful
# recording produces a small journal rather than a large empty one.
EVENT_GAP = "gap"
EVENT_TIMESTAMP_REGRESSION = "timestamp_regression"
EVENT_CLOCK_JUMP = "clock_jump"
EVENT_SUMMARY = "summary"

# Timing observation names.
OBSERVATION_CLOCK_OFFSET = "clock_offset"
OBSERVATION_WALL_CLOCK_ANCHOR = "wall_clock_anchor"


def quality_record(
    *,
    event: str,
    monotonic: float,
    plugin_key: str = "",
    stream_key: str = "",
    profile_version: int = QUALITY_PROFILE_VERSION,
    **details: Any,
) -> dict[str, Any]:
    """One ``quality.jsonl`` line.

    ``monotonic`` rather than wall time: target doc §6 allows the wall clock
    only as one UTC anchor at session start and end, which
    ``timing_record(OBSERVATION_WALL_CLOCK_ANCHOR, ...)`` writes. Everything
    else is a duration from a clock that cannot jump.
    """
    return {
        "schema": QUALITY_JOURNAL_SCHEMA,
        "profile_version": int(profile_version),
        "event": str(event),
        "monotonic": float(monotonic),
        "plugin_key": str(plugin_key),
        "stream_key": str(stream_key),
        "details": {key: value for key, value in details.items() if value is not None},
    }


def timing_record(
    *,
    observation: str,
    monotonic: float,
    plugin_key: str = "",
    stream_key: str = "",
    **details: Any,
) -> dict[str, Any]:
    """One ``timing.jsonl`` line."""
    return {
        "schema": TIMING_JOURNAL_SCHEMA,
        "observation": str(observation),
        "monotonic": float(monotonic),
        "plugin_key": str(plugin_key),
        "stream_key": str(stream_key),
        "details": {key: value for key, value in details.items() if value is not None},
    }


class StreamQualityObserver:
    """Streaming counters for one LSL stream, judged against a profile.

    Fed the chunks the ingest loop already pulls, so it adds no second read
    path over the data. Keeps only running aggregates -- never the samples
    themselves -- because a recording must not need memory proportional to
    its own length to describe its own quality.
    """

    def __init__(
        self,
        *,
        plugin_key: str,
        stream_key: str,
        nominal_rate_hz: float,
        profile: Mapping[str, Any] | None = None,
    ) -> None:
        self.plugin_key = str(plugin_key)
        self.stream_key = str(stream_key)
        self.nominal_rate_hz = float(nominal_rate_hz or 0.0)
        self._profile = dict(profile or DEFAULT_QUALITY_PROFILE)
        self.sample_count = 0
        self.gap_count = 0
        self.regression_count = 0
        self.first_timestamp: float | None = None
        self.last_timestamp: float | None = None
        # Welford's online algorithm rather than sum/sum-of-squares: the
        # naive form cancels catastrophically here, because sample intervals
        # are tiny numbers that barely differ (a 250 Hz stream's deltas are
        # all ~0.004). It reported 1.3 ns of jitter on a perfectly even
        # stream -- false precision in exactly the number a researcher would
        # read as timing quality.
        self._delta_count = 0
        self._delta_mean = 0.0
        self._delta_m2 = 0.0
        self._largest_gap_seconds = 0.0

    @property
    def profile_version(self) -> int:
        return int(self._profile.get("version") or QUALITY_PROFILE_VERSION)

    @property
    def expected_period_seconds(self) -> float | None:
        """``None`` for an irregular stream, which cannot have a gap."""
        if self.nominal_rate_hz <= 0 or not math.isfinite(self.nominal_rate_hz):
            return None
        return 1.0 / self.nominal_rate_hz

    def observe_chunk(
        self,
        timestamps: Sequence[float],
        *,
        monotonic: float,
    ) -> list[dict[str, Any]]:
        """Fold one pulled chunk in; return the events it triggered."""
        events: list[dict[str, Any]] = []
        period = self.expected_period_seconds
        gap_threshold = (
            period * float(self._profile.get("gap_periods") or 0.0) if period else None
        )
        for raw in timestamps:
            try:
                timestamp = float(raw)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(timestamp):
                continue
            previous = self.last_timestamp
            self.sample_count += 1
            if self.first_timestamp is None:
                self.first_timestamp = timestamp
            self.last_timestamp = timestamp
            if previous is None:
                continue
            delta = timestamp - previous
            if delta <= 0:
                # Never folded into the jitter aggregates: a regression is a
                # defect to report, not a sample interval to average over.
                self.regression_count += 1
                events.append(
                    self._event(
                        EVENT_TIMESTAMP_REGRESSION,
                        monotonic,
                        delta_seconds=delta,
                        previous_timestamp=previous,
                        timestamp=timestamp,
                    )
                )
                continue
            self._delta_count += 1
            difference = delta - self._delta_mean
            self._delta_mean += difference / self._delta_count
            self._delta_m2 += difference * (delta - self._delta_mean)
            if gap_threshold is not None and delta > gap_threshold:
                self.gap_count += 1
                self._largest_gap_seconds = max(self._largest_gap_seconds, delta)
                events.append(
                    self._event(
                        EVENT_GAP,
                        monotonic,
                        gap_seconds=delta,
                        expected_period_seconds=period,
                        missed_periods=round(delta / period, 3) if period else None,
                    )
                )
        return events

    def summary(self, *, monotonic: float) -> dict[str, Any]:
        """A periodic snapshot of the aggregates, for the same journal."""
        return self._event(
            EVENT_SUMMARY,
            monotonic,
            sample_count=self.sample_count,
            gap_count=self.gap_count,
            timestamp_regression_count=self.regression_count,
            nominal_rate_hz=self.nominal_rate_hz,
            effective_rate_hz=self.effective_rate_hz(),
            jitter_seconds=self.jitter_seconds(),
            largest_gap_seconds=self._largest_gap_seconds or None,
        )

    def effective_rate_hz(self) -> float | None:
        """Samples per second actually seen, or ``None`` when unknowable."""
        if self.first_timestamp is None or self.last_timestamp is None:
            return None
        elapsed = self.last_timestamp - self.first_timestamp
        if elapsed <= 0 or self.sample_count < 2:
            return None
        return (self.sample_count - 1) / elapsed

    def jitter_seconds(self) -> float | None:
        """Standard deviation of sample intervals; ``None`` below 2 intervals."""
        if self._delta_count < 2:
            return None
        variance = self._delta_m2 / self._delta_count
        return math.sqrt(variance) if variance > 0 else 0.0

    def _event(self, event: str, monotonic: float, **details: Any) -> dict[str, Any]:
        return quality_record(
            event=event,
            monotonic=monotonic,
            plugin_key=self.plugin_key,
            stream_key=self.stream_key,
            profile_version=self.profile_version,
            **details,
        )


class WallClockJumpDetector:
    """Notice the system clock moving independently of the monotonic clock.

    Target doc §6: "Ein Sprung der Systemuhr während der Aufnahme, etwa durch
    NTP-Korrektur oder Zeitumstellung, wird erkannt und als Qualitätsereignis
    protokolliert." Recording never *depends* on wall time -- that is why
    durations use the monotonic clock -- but a jump still has to be on record,
    because the session's UTC anchors were taken from the clock that moved.
    """

    def __init__(self, *, profile: Mapping[str, Any] | None = None) -> None:
        self._profile = dict(profile or DEFAULT_QUALITY_PROFILE)
        self._previous: tuple[float, float] | None = None

    def observe(self, *, monotonic: float, wall: float) -> dict[str, Any] | None:
        previous = self._previous
        self._previous = (monotonic, wall)
        if previous is None:
            return None
        monotonic_elapsed = monotonic - previous[0]
        wall_elapsed = wall - previous[1]
        drift = wall_elapsed - monotonic_elapsed
        threshold = float(self._profile.get("clock_jump_seconds") or 0.0)
        if threshold <= 0 or abs(drift) <= threshold:
            return None
        return quality_record(
            event=EVENT_CLOCK_JUMP,
            monotonic=monotonic,
            profile_version=int(self._profile.get("version") or QUALITY_PROFILE_VERSION),
            drift_seconds=drift,
            monotonic_elapsed_seconds=monotonic_elapsed,
            wall_elapsed_seconds=wall_elapsed,
        )


def summarize_quality_journal(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Count what a finished ``quality.jsonl`` contains, for finalization.

    Kept here rather than in the host so the counting rules live next to the
    writing rules and cannot drift apart.
    """
    counts: dict[str, int] = {}
    streams: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        event = str(record.get("event") or "")
        if not event:
            continue
        counts[event] = counts.get(event, 0) + 1
        plugin_key = str(record.get("plugin_key") or "")
        stream_key = str(record.get("stream_key") or "")
        if plugin_key and stream_key:
            streams.add(f"{plugin_key}.{stream_key}")
    return {
        "event_counts": counts,
        "streams": sorted(streams),
        "has_events": any(event != EVENT_SUMMARY for event in counts),
    }
