"""Slowest-grid backup projection scheduling.

The backup stream is a derived QC/recovery stream.  It samples the latest
cached projection at fixed monotonic deadlines; it never polls hardware at a
deadline and never carries stale values forward.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


STATUS_MISSING = 0.0
STATUS_VALID = 1.0
STATUS_STALE = 2.0
STATUS_DEGRADED = 3.0
# Compatibility aliases for plans created during the v2 prototype.
STATUS_OK = STATUS_VALID
STATUS_SOURCE_DEGRADED = STATUS_DEGRADED


@dataclass(frozen=True)
class BackupChannel:
    """One manifest-declared source channel projected to a backup output."""

    output: str
    source_channel: str

    def __post_init__(self) -> None:
        if not self.output.strip() or not self.source_channel.strip():
            raise ValueError("backup output and source channel names are required")


@dataclass(frozen=True)
class BackupProjection:
    plugin_key: str
    stream_id: str
    rate_hz: float
    channels: tuple[BackupChannel, ...]
    stale_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.plugin_key.strip():
            raise ValueError("plugin_key is required")
        if not self.stream_id.strip():
            raise ValueError("stream_id is required")
        if not math.isfinite(self.rate_hz) or self.rate_hz <= 0:
            raise ValueError("backup projection rate_hz must be finite and positive")
        if not self.channels:
            raise ValueError("backup projection needs named numeric channels")
        outputs = [channel.output for channel in self.channels]
        if len(set(outputs)) != len(outputs):
            raise ValueError("backup projection output names must be unique")
        if self.stale_after_seconds is not None and (
            not math.isfinite(self.stale_after_seconds) or self.stale_after_seconds <= 0
        ):
            raise ValueError("stale_after_seconds must be finite and positive")

    @property
    def freshness_window(self) -> float:
        # Two native periods is a conservative default.  Plugins may declare a
        # wider threshold when transport buffering is expected.
        return self.stale_after_seconds or (2.0 / self.rate_hz)

    @property
    def cache_key(self) -> str:
        return f"{self.plugin_key}:{self.stream_id}"


@dataclass(frozen=True)
class CachedProjection:
    values: tuple[float, ...]
    received_monotonic: float
    source_timestamp: float | None
    sequence: int | None
    source_ok: bool


@dataclass(frozen=True)
class BackupFrame:
    """One fixed-grid numeric frame ready for an XDF stream writer."""

    deadline_monotonic: float
    values: Mapping[str, float]


def choose_backup_rate(projections: Sequence[BackupProjection]) -> float:
    """Choose the smallest positive declared projection rate once per session."""

    if not projections:
        raise ValueError("at least one backup projection is required")
    return min(projection.rate_hz for projection in projections)


def projections_from_manifest(plugin_key: str, payload: Mapping[str, Any]) -> tuple[BackupProjection, ...]:
    """Normalize the v3 ``backup_projection`` capability into stream caches.

    Stable manifest shape::

        {"rate_hz": 1, "stale_after_ms": 2500,
         "channels": [{"output": "hr", "stream": "vitals", "channel": "heart_rate"}]}
    """

    try:
        rate_hz = float(payload["rate_hz"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("backup_projection.rate_hz must be a positive number") from error
    stale_raw = payload.get("stale_after_ms")
    try:
        stale_after_seconds = float(stale_raw) / 1000.0 if stale_raw is not None else None
    except (TypeError, ValueError) as error:
        raise ValueError("backup_projection.stale_after_ms must be a positive number") from error
    raw_channels = payload.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise ValueError("backup_projection.channels must be a non-empty list")

    grouped: dict[str, list[BackupChannel]] = {}
    all_outputs: set[str] = set()
    for raw_channel in raw_channels:
        if not isinstance(raw_channel, dict):
            raise ValueError("backup projection channels must be objects")
        stream_id = str(raw_channel.get("stream") or "").strip()
        output = str(raw_channel.get("output") or "").strip()
        source_channel = str(raw_channel.get("channel") or "").strip()
        if not stream_id:
            raise ValueError("backup projection channel.stream is required")
        channel = BackupChannel(output=output, source_channel=source_channel)
        if output in all_outputs:
            raise ValueError(f"duplicate backup projection output: {output}")
        all_outputs.add(output)
        grouped.setdefault(stream_id, []).append(channel)

    return tuple(
        BackupProjection(
            plugin_key=plugin_key,
            stream_id=stream_id,
            rate_hz=rate_hz,
            channels=tuple(channels),
            stale_after_seconds=stale_after_seconds,
        )
        for stream_id, channels in grouped.items()
    )


class BackupSampler:
    """Caches projection values and emits them on one monotonic fixed grid."""

    def __init__(self, projections: Sequence[BackupProjection], *, start_monotonic: float) -> None:
        if not math.isfinite(start_monotonic):
            raise ValueError("start_monotonic must be finite")
        self.projections = tuple(projections)
        self.rate_hz = choose_backup_rate(self.projections)
        self.period_seconds = 1.0 / self.rate_hz
        self._next_deadline = start_monotonic + self.period_seconds
        self._cache: dict[str, CachedProjection] = {}

        keys = [projection.cache_key for projection in self.projections]
        if len(keys) != len(set(keys)):
            raise ValueError("backup projection plugin/stream pairs must be unique")

    @property
    def next_deadline(self) -> float:
        return self._next_deadline

    @property
    def channel_names(self) -> tuple[str, ...]:
        """Stable XDF channel order, including validity/QC fields."""

        names: list[str] = []
        for projection in self.projections:
            prefix = f"{projection.plugin_key}.{projection.stream_id}"
            names.extend(f"{prefix}.{channel.output}" for channel in projection.channels)
            names.extend(
                (
                    f"{prefix}.valid",
                    f"{prefix}.sample_age_ms",
                    f"{prefix}.sequence",
                    f"{prefix}.status",
                )
            )
        return tuple(names)

    def stream_metadata(self) -> dict[str, object]:
        """Metadata that must accompany the derived backup stream."""

        return {
            "artifact_role": "derived_backup",
            "rate_hz": self.rate_hz,
            "resampling_strategy": "latest_cached_at_slowest_projection_grid; stale_to_nan",
            "status_codes": {
                "missing": STATUS_MISSING,
                "valid": STATUS_VALID,
                "stale": STATUS_STALE,
                "degraded": STATUS_DEGRADED,
            },
            "projections": [
                {
                    "plugin_key": projection.plugin_key,
                    "stream_id": projection.stream_id,
                    "source_rate_hz": projection.rate_hz,
                    "stale_after_seconds": projection.freshness_window,
                    "channels": [
                        {"output": channel.output, "source_channel": channel.source_channel}
                        for channel in projection.channels
                    ],
                }
                for projection in self.projections
            ],
        }

    def update(
        self,
        plugin_key: str,
        stream_id: str,
        values: Mapping[str, float],
        *,
        received_monotonic: float,
        source_timestamp: float | None = None,
        sequence: int | None = None,
        source_ok: bool = True,
    ) -> None:
        projection = self._projection(plugin_key, stream_id)
        if not math.isfinite(received_monotonic):
            raise ValueError("received_monotonic must be finite")
        source_channels = {channel.source_channel for channel in projection.channels}
        missing = [channel for channel in source_channels if channel not in values]
        unexpected = [channel for channel in values if channel not in source_channels]
        if missing or unexpected:
            raise ValueError(f"projection channel mismatch (missing={missing}, unexpected={unexpected})")
        numeric_values: list[float] = []
        for channel in projection.channels:
            raw_value = values[channel.source_channel]
            if isinstance(raw_value, bool):
                numeric_values.append(1.0 if raw_value else 0.0)
                continue
            try:
                numeric_values.append(float(raw_value))
            except (TypeError, ValueError) as error:
                raise ValueError(f"backup channel {channel.source_channel!r} is not numeric") from error
        self._cache[projection.cache_key] = CachedProjection(
            values=tuple(numeric_values),
            received_monotonic=received_monotonic,
            source_timestamp=source_timestamp,
            sequence=sequence,
            source_ok=bool(source_ok),
        )

    def emit_due(self, now_monotonic: float) -> list[BackupFrame]:
        """Emit every elapsed grid point without changing the fixed cadence."""

        if not math.isfinite(now_monotonic):
            raise ValueError("now_monotonic must be finite")
        frames: list[BackupFrame] = []
        # Tiny tolerance prevents losing a deadline to floating-point rounding.
        while self._next_deadline <= now_monotonic + 1e-12:
            frames.append(self._build_frame(self._next_deadline))
            self._next_deadline += self.period_seconds
        return frames

    def _projection(self, plugin_key: str, stream_id: str) -> BackupProjection:
        cache_key = f"{plugin_key}:{stream_id}"
        for projection in self.projections:
            if projection.cache_key == cache_key:
                return projection
        raise KeyError(f"unknown backup projection: {cache_key}")

    def _build_frame(self, deadline: float) -> BackupFrame:
        output: dict[str, float] = {}
        for projection in self.projections:
            prefix = f"{projection.plugin_key}.{projection.stream_id}"
            cached = self._cache.get(projection.cache_key)
            if cached is None:
                self._write_missing(output, prefix, projection, age_ms=math.nan, sequence=None, status=STATUS_MISSING)
                continue

            age_seconds = max(0.0, deadline - cached.received_monotonic)
            is_future = cached.received_monotonic > deadline + 1e-12
            is_stale = is_future or age_seconds > projection.freshness_window
            if is_stale:
                self._write_missing(
                    output,
                    prefix,
                    projection,
                    age_ms=age_seconds * 1000.0,
                    sequence=cached.sequence,
                    status=STATUS_STALE,
                )
                continue

            for channel, value in zip(projection.channels, cached.values):
                output[f"{prefix}.{channel.output}"] = value
            output[f"{prefix}.valid"] = 1.0 if cached.source_ok else 0.0
            output[f"{prefix}.sample_age_ms"] = age_seconds * 1000.0
            output[f"{prefix}.sequence"] = float(cached.sequence) if cached.sequence is not None else math.nan
            output[f"{prefix}.status"] = STATUS_VALID if cached.source_ok else STATUS_DEGRADED

        return BackupFrame(deadline_monotonic=deadline, values=output)

    @staticmethod
    def _write_missing(
        output: dict[str, float],
        prefix: str,
        projection: BackupProjection,
        *,
        age_ms: float,
        sequence: int | None,
        status: float,
    ) -> None:
        for channel in projection.channels:
            output[f"{prefix}.{channel.output}"] = math.nan
        output[f"{prefix}.valid"] = 0.0
        output[f"{prefix}.sample_age_ms"] = age_ms
        output[f"{prefix}.sequence"] = float(sequence) if sequence is not None else math.nan
        output[f"{prefix}.status"] = status
