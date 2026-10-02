"""The live view of the sensor data contract: what the dashboard draws.

``LiveView`` runs in the plugin's own process. ``SensorStreams`` hands it
every row it publishes, so the dashboard shows exactly the values that were
recorded -- averaged per 0.5 s over the last 60 s, never anything extra.
Nothing here is ever recorded.

``standardize_live`` runs in the host: it forces the one shape every plugin
reports (whatever the plugin sent), turns NaN into null for the browser, and
empties the series while the plugin is not running.
"""
from __future__ import annotations

from collections import deque
import math
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from study_runner.contracts.sensor_contract import (
    LIVE_VIEW_BUCKET_S,
    LIVE_VIEW_POINTS,
    LIVE_VIEW_RATE_HZ,
    LIVE_VIEW_WINDOW_S,
)


class _Bucket:
    __slots__ = ("index", "sums", "counts", "valid")

    def __init__(self, index: int, size: int) -> None:
        self.index = index
        self.sums = [0.0] * size
        self.counts = [0] * size
        # None: no sample said anything about validity.
        self.valid: bool | None = None


class LiveView:
    """Mean per 0.5 s bucket of the declared channels, last 60 s."""

    def __init__(
        self,
        series: Iterable[Mapping[str, Any]],
        streams: Mapping[str, Mapping[str, Any]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._series: list[dict[str, Any]] = []
        self._by_stream: dict[str, list[dict[str, Any]]] = {}
        self._declared = {key: tuple(stream["channels"]) for key, stream in streams.items()}
        # (stream, row channel names) -> [(series, row position per channel or -1)]
        self._positions: dict[tuple[str, tuple[str, ...]], list[tuple[dict[str, Any], list[int]]]] = {}
        for item in series:
            spec = {"key": item["key"], "stream": item["stream"], "channels": list(item["channels"])}
            self._series.append(spec)
            self._by_stream.setdefault(item["stream"], []).append(spec)
        self._buckets: dict[str, deque[_Bucket]] = {}
        self._flagged: set[str] = set()
        self.reset()

    def reset(self) -> None:
        """Forget every value (a new run starts empty)."""
        with self._lock:
            self._buckets = {spec["key"]: deque(maxlen=LIVE_VIEW_POINTS + 1) for spec in self._series}
            self._flagged = set()

    def shows(self, stream: str) -> bool:
        return stream in self._by_stream

    def observe(
        self,
        stream: str,
        row: list[Any],
        *,
        valid: bool | None = None,
        channels: Iterable[str] | None = None,
        at: float | None = None,
    ) -> None:
        """Take one published row of ``stream`` (``channels``: the row's names
        when they differ from the manifest, e.g. a device's own channel list)."""
        if stream not in self._by_stream:
            return
        targets = self._targets(stream, tuple(channels) if channels is not None else self._declared[stream])
        index = int((self._clock() if at is None else at) // LIVE_VIEW_BUCKET_S)
        with self._lock:
            for spec, positions in targets:
                bucket = self._bucket(spec["key"], index, len(positions))
                if bucket is None:
                    continue
                for slot, position in enumerate(positions):
                    value = _finite(row[position] if 0 <= position < len(row) else None)
                    if value is not None:
                        bucket.sums[slot] += value
                        bucket.counts[slot] += 1
                if valid is not None:
                    self._flagged.add(spec["key"])
                    bucket.valid = bool(valid) if bucket.valid is None else (bucket.valid and bool(valid))

    def _targets(self, stream: str, names: tuple[str, ...]) -> list[tuple[dict[str, Any], list[int]]]:
        cache_key = (stream, names)
        targets = self._positions.get(cache_key)
        if targets is None:
            targets = [
                (spec, [names.index(name) if name in names else -1 for name in spec["channels"]])
                for spec in self._by_stream[stream]
            ]
            self._positions[cache_key] = targets
        return targets

    def invalidate(self, keys: Iterable[str] | None = None) -> None:
        """Mark the current bucket of these series (all when None) as not valid."""
        wanted = set(keys) if keys is not None else {spec["key"] for spec in self._series}
        index = int(self._clock() // LIVE_VIEW_BUCKET_S)
        with self._lock:
            for spec in self._series:
                if spec["key"] not in wanted:
                    continue
                bucket = self._bucket(spec["key"], index, len(spec["channels"]))
                if bucket is not None:
                    self._flagged.add(spec["key"])
                    bucket.valid = False

    def _bucket(self, key: str, index: int, size: int) -> _Bucket | None:
        buckets = self._buckets[key]
        if buckets and buckets[-1].index == index:
            return buckets[-1]
        if buckets and index < buckets[-1].index:
            # A late row (rare): add it to its own bucket if still kept.
            return next((bucket for bucket in reversed(buckets) if bucket.index == index), None)
        bucket = _Bucket(index, size)
        buckets.append(bucket)
        return bucket

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        """The last 120 completed buckets per series; index 119 is the newest."""
        current = int((self._clock() if now is None else now) // LIVE_VIEW_BUCKET_S)
        first = current - LIVE_VIEW_POINTS
        series: dict[str, Any] = {}
        with self._lock:
            for spec in self._series:
                by_index = {bucket.index: bucket for bucket in self._buckets[spec["key"]]}
                values: dict[str, list[float | None]] = {name: [] for name in spec["channels"]}
                valid: list[bool | None] = []
                for index in range(first, current):
                    bucket = by_index.get(index)
                    for slot, name in enumerate(spec["channels"]):
                        count = bucket.counts[slot] if bucket else 0
                        values[name].append(round(bucket.sums[slot] / count, 6) if count else None)
                    valid.append(bucket.valid if bucket else None)
                series[spec["key"]] = {
                    "stream": spec["stream"],
                    "channels": values,
                    "valid": valid if spec["key"] in self._flagged else None,
                }
        return {
            "rate_hz": LIVE_VIEW_RATE_HZ,
            "window_s": LIVE_VIEW_WINDOW_S,
            "points": LIVE_VIEW_POINTS,
            "series": series,
        }


def empty_live(series: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The live block of a plugin that shows nothing right now."""
    return {
        "rate_hz": LIVE_VIEW_RATE_HZ,
        "window_s": LIVE_VIEW_WINDOW_S,
        "points": LIVE_VIEW_POINTS,
        "series": {
            item["key"]: {
                "stream": item["stream"],
                "channels": {name: [None] * LIVE_VIEW_POINTS for name in item["channels"]},
                "valid": None,
            }
            for item in series
        },
    }


def standardize_live(raw: Any, series: Iterable[Mapping[str, Any]], *, running: bool) -> dict[str, Any]:
    """The one live shape the dashboard reads, whatever the plugin sent.

    Only declared series and channels survive, every list has exactly 120
    numbers or nulls (NaN and infinities become null), and a plugin that
    does not run shows empty series.
    """
    declared = list(series)
    result = empty_live(declared)
    if not running or not isinstance(raw, Mapping):
        return result
    incoming = raw.get("series")
    if not isinstance(incoming, Mapping):
        return result
    for item in declared:
        source = incoming.get(item["key"])
        if not isinstance(source, Mapping):
            continue
        channels = source.get("channels") if isinstance(source.get("channels"), Mapping) else {}
        target = result["series"][item["key"]]
        for name in item["channels"]:
            target["channels"][name] = _points(channels.get(name), _finite)
        if isinstance(source.get("valid"), list):
            target["valid"] = _points(source["valid"], lambda value: value if isinstance(value, bool) else None)
    return result


def _points(values: Any, convert: Callable[[Any], Any]) -> list[Any]:
    items = list(values)[-LIVE_VIEW_POINTS:] if isinstance(values, list) else []
    return [None] * (LIVE_VIEW_POINTS - len(items)) + [convert(value) for value in items]


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
