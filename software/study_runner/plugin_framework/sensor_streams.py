"""The one way a sensor plugin publishes data (the sensor data contract).

An adapter creates its streams once, from its own manifest::

    _streams = SensorStreams.for_plugin(__file__)

    _streams.configure(name_prefix="AmHub")   # the LSL name prefix setting
    _streams.open_all()                       # one outlet per manifest stream
    arrival = _streams.now()                  # LSL clock, as early as possible
    _streams.push("radar", row, arrival)      # one sample per real frame

Every push goes to the stream's LSL outlet (the recording worker writes it
to XDF; the 1 Hz backup is sampled there too) and to the live view the
dashboard draws. There is no other path: plugins never build outlets
themselves (software/tests/test_sensor_data_contract.py checks this).

Guarantees, the same for every sensor:

- Outlets are built only from the manifest (name, type, rate, format,
  channels, units, source_id, desc block). A runtime channel list (BrainBit's
  device channels) can replace the declared one; identity never changes.
- Every sample carries an explicit timestamp, and a stream's timestamps
  never go backwards (a smaller one is raised to the last one and counted
  as ``clamped``).
- A stream with a correction channel stays reversible: the helper writes
  ``arrival - timestamp`` in milliseconds into it, so the arrival time can
  always be rebuilt from the XDF.
- A failed push is counted, never raised; the core reports the plugin as
  failed until its next start (``stream_health``).
- ``reset()`` (called by the core before start/stop/restart) empties the live
  view and the counters, so nothing carries over into the next run.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

from study_runner.contracts.sensor_contract import live_view_series
from study_runner.contracts.stream_contract import build_stream_info, load_own_manifest

from .live_view import LiveView

_REGISTRY: dict[str, "SensorStreams"] = {}
_REGISTRY_LOCK = threading.Lock()


def registered(plugin_key: str) -> "SensorStreams | None":
    """The streams a plugin's adapter created in this process, if any."""
    with _REGISTRY_LOCK:
        return _REGISTRY.get(str(plugin_key or ""))


@dataclass
class _Outlet:
    contract: dict[str, Any]
    outlet: Any
    signature: tuple[Any, ...]
    name: str
    correction_index: int | None = None
    string_stream: bool = False


@dataclass
class _Health:
    open: bool = False
    pushed: int = 0
    failures: int = 0
    rejected: int = 0
    clamped: int = 0
    last_error: str = ""
    last_error_epoch: float | None = None
    last_push_epoch: float | None = None
    last_rejection: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "open": self.open,
            "pushed": self.pushed,
            "failures": self.failures,
            "rejected": self.rejected,
            "clamped": self.clamped,
            "last_error": self.last_error,
            "last_push_epoch": self.last_push_epoch,
            "last_rejection": self.last_rejection,
        }


class SensorStreams:
    """Raw LSL outlets plus the live view of one sensor plugin."""

    def __init__(self, manifest: Mapping[str, Any], *, backend: Any = None) -> None:
        self.plugin_key = str(manifest["plugin_key"])
        self._contracts: dict[str, dict[str, Any]] = {
            str(stream["key"]): dict(stream) for stream in manifest.get("streams") or []
        }
        self._live = LiveView(live_view_series(manifest), self._contracts)
        self._lock = threading.RLock()
        self._backend = backend
        self._backend_missing = False
        self._prefix = self.plugin_key
        self._auto_install = True
        self._outlets: dict[str, _Outlet] = {}
        # Per stream for the life of the process: an outlet outlives a run.
        self._last_timestamp: dict[str, float] = {}
        self._health: dict[str, _Health] = {key: _Health() for key in self._contracts}

    @classmethod
    def for_plugin(cls, adapter_file: str, *, backend: Any = None) -> "SensorStreams":
        """Streams for the plugin whose ``manifest.json`` sits beside ``adapter_file``."""
        instance = cls(load_own_manifest(adapter_file), backend=backend)
        with _REGISTRY_LOCK:
            _REGISTRY[instance.plugin_key] = instance
        return instance

    # ------------------------------------------------------------ set-up

    def configure(self, *, name_prefix: str | None = None, auto_install: bool | None = None) -> None:
        """The LSL name prefix (``<prefix>_<TYPE>``) and whether pylsl may be installed."""
        with self._lock:
            if name_prefix:
                self._prefix = str(name_prefix)
            if auto_install is not None:
                self._auto_install = bool(auto_install)
            self._backend_missing = False

    def use_backend(self, backend: Any) -> None:
        """Replace pylsl (tests); closes every outlet. A new backend is a new
        clock, so the last timestamps of the old one are forgotten."""
        with self._lock:
            self.close()
            self._backend = backend
            self._backend_missing = False
            self._last_timestamp.clear()

    def _pylsl(self) -> Any | None:
        if self._backend is not None:
            return self._backend
        if self._backend_missing:
            return None
        from study_runner.shared.dependency_utils import ensure_requirements

        if not ensure_requirements(
            [("pylsl", "pylsl")],
            auto_install=self._auto_install,
            label=f"{self.plugin_key} LSL",
        ):
            self._backend_missing = True
            return None
        import pylsl

        self._backend = pylsl
        return pylsl

    def now(self) -> float:
        """This computer's LSL clock; take it the moment a sample arrives."""
        backend = self._pylsl()
        if backend is None:
            return time.monotonic()
        return float(backend.local_clock())

    def declared(self, key: str) -> dict[str, Any]:
        return dict(self._contract(key))

    def declared_contracts(self) -> dict[str, dict[str, Any]]:
        """Every stream the manifest declares, by key (manifest order)."""
        return {key: dict(contract) for key, contract in self._contracts.items()}

    def _contract(self, key: str) -> dict[str, Any]:
        try:
            return self._contracts[key]
        except KeyError:
            raise ValueError(f"{self.plugin_key} declares no stream {key!r}") from None

    def open(
        self,
        key: str,
        *,
        channels: Sequence[str] | None = None,
        channel_units: Sequence[str] | None = None,
        nominal_rate_hz: float | None = None,
    ) -> bool:
        """Open one outlet (again only when its channels, rate or name change)."""
        contract = self._resolved(key, channels, channel_units, nominal_rate_hz)
        with self._lock:
            name = str(contract.get("name") or f"{self._prefix}_{contract.get('type') or key}")
            signature = (
                tuple(contract["channels"]),
                tuple(contract["channel_units"]),
                float(contract.get("nominal_rate_hz") or 0.0),
                name,
            )
            existing = self._outlets.get(key)
            if existing is not None and existing.signature == signature:
                return True
            backend = self._pylsl()
            if backend is None:
                self._fail(key, "pylsl is not available, so no LSL outlet can be opened")
                return False
            if existing is not None:
                self._close_one(key)
            try:
                info = build_stream_info(backend, contract, name=name)
                outlet = backend.StreamOutlet(info)
            except Exception as error:
                self._fail(key, f"could not open the {key} outlet: {error}")
                return False
            correction = (contract.get("timing") or {}).get("correction_channel")
            self._outlets[key] = _Outlet(
                contract=contract,
                outlet=outlet,
                signature=signature,
                name=name,
                correction_index=contract["channels"].index(correction) if correction in contract["channels"] else None,
                string_stream=contract.get("channel_format") == "string",
            )
            self._health[key].open = True
            return True

    def open_all(self) -> bool:
        """Open every declared stream with its declared channels."""
        return all([self.open(key) for key in self._contracts])

    def _resolved(
        self,
        key: str,
        channels: Sequence[str] | None,
        channel_units: Sequence[str] | None,
        nominal_rate_hz: float | None,
    ) -> dict[str, Any]:
        declared = self._contract(key)
        contract = dict(declared)
        if channels is not None:
            names = [str(name) for name in channels]
            if not names or len(set(names)) != len(names):
                raise ValueError(f"{key}: runtime channels must be unique and non-empty")
            if channel_units is None:
                units = list(declared["channel_units"])
                if len(set(units)) != 1:
                    raise ValueError(f"{key}: give channel_units for a runtime channel list")
                channel_units = [units[0]] * len(names)
            if len(channel_units) != len(names):
                raise ValueError(f"{key}: one unit per runtime channel")
            contract["channels"] = names
            contract["channel_units"] = [str(unit) for unit in channel_units]
        if nominal_rate_hz is not None:
            contract["nominal_rate_hz"] = float(nominal_rate_hz)
        return contract

    def is_open(self, key: str) -> bool:
        with self._lock:
            return key in self._outlets

    def contracts(self) -> list[dict[str, Any]]:
        """The streams actually open, with their real channels (manifest order)."""
        with self._lock:
            return [
                {**self._outlets[key].contract, "name": self._outlets[key].name}
                for key in self._contracts
                if key in self._outlets
            ]

    def close(self, key: str | None = None) -> None:
        """Close one outlet, or all of them."""
        with self._lock:
            for name in [key] if key is not None else list(self._outlets):
                self._close_one(name)

    def _close_one(self, key: str) -> None:
        entry = self._outlets.pop(key, None)
        if key in self._health:
            self._health[key].open = False
        # pylsl destroys the outlet with the object.
        del entry

    # ------------------------------------------------------------- data

    def row(self, key: str, values: Mapping[str, Any]) -> list[Any]:
        """``values`` in the stream's channel order; a missing value is NaN ("" for text)."""
        with self._lock:
            entry = self._outlets.get(key)
            contract = entry.contract if entry is not None else self._contract(key)
        if contract.get("channel_format") == "string":
            return ["" if values.get(name) is None else str(values.get(name)) for name in contract["channels"]]
        return [_number(values.get(name)) for name in contract["channels"]]

    def channels(self, key: str) -> list[str]:
        """The channel names of an open stream (the declared ones otherwise)."""
        with self._lock:
            entry = self._outlets.get(key)
            return list((entry.contract if entry is not None else self._contract(key))["channels"])

    def push(
        self,
        key: str,
        row: Sequence[Any],
        timestamp: float,
        *,
        arrival: float | None = None,
        valid: bool | None = None,
    ) -> bool:
        """Publish one sample. ``arrival`` (LSL clock) is needed when ``timestamp`` was corrected."""
        result = self._publish(key, [list(row)], [timestamp], [arrival], chunk=False)
        if result is None:
            return False
        rows, channels = result
        self._live.observe(key, rows[0], valid=valid, channels=channels)
        return True

    def push_chunk(
        self,
        key: str,
        rows: Sequence[Sequence[Any]],
        timestamps: Sequence[float],
        *,
        arrivals: Sequence[float | None] | None = None,
        valid: bool | None = None,
    ) -> bool:
        """Publish several samples at once, each with its own timestamp."""
        if not rows:
            return True
        if len(rows) != len(timestamps) or (arrivals is not None and len(arrivals) != len(rows)):
            self._fail(key, f"{key}: rows, timestamps and arrivals differ in length")
            return False
        result = self._publish(
            key,
            [list(row) for row in rows],
            list(timestamps),
            list(arrivals) if arrivals is not None else [None] * len(rows),
            chunk=True,
        )
        if result is None:
            return False
        published, channels = result
        if self._live.shows(key):
            for row in published:
                self._live.observe(key, row, valid=valid, channels=channels)
        return True

    def _publish(
        self,
        key: str,
        rows: list[list[Any]],
        timestamps: list[float],
        arrivals: list[float | None],
        *,
        chunk: bool,
    ) -> tuple[list[list[Any]], list[str]] | None:
        with self._lock:
            self._contract(key)
            entry = self._outlets.get(key)
            if entry is None:
                self._fail(key, f"the {key} stream is not open")
                return None
            width = len(entry.contract["channels"])
            health = self._health[key]
            stamps: list[float] = []
            last = self._last_timestamp.get(key)
            for row, raw_stamp, arrival in zip(rows, timestamps, arrivals):
                if len(row) != width:
                    self._fail(key, f"{key}: {len(row)} values for {width} channels")
                    return None
                stamp = float(raw_stamp)
                if not math.isfinite(stamp):
                    self._fail(key, f"{key}: the timestamp is not a finite number")
                    return None
                if arrival is not None:
                    # A timestamp can be earlier than the arrival, never later.
                    stamp = min(stamp, float(arrival))
                if last is not None and stamp < last:
                    stamp = last
                    health.clamped += 1
                if entry.correction_index is not None:
                    reference = stamp if arrival is None else float(arrival)
                    row[entry.correction_index] = (reference - stamp) * 1000.0
                stamps.append(stamp)
                last = stamp
            try:
                if chunk:
                    entry.outlet.push_chunk(rows, stamps)
                else:
                    entry.outlet.push_sample(rows[0], stamps[0])
            except Exception as error:
                self._fail(key, f"{key} publication failed: {error}")
                return None
            self._last_timestamp[key] = stamps[-1]
            health.pushed += len(rows)
            health.last_push_epoch = time.time()
            return rows, list(entry.contract["channels"])

    def reject(self, key: str, reason: str) -> None:
        """Count a sample the adapter refused to publish (not a failure)."""
        with self._lock:
            health = self._health[self._key(key)]
            health.rejected += 1
            health.last_rejection = str(reason)

    def invalidate_live(self, keys: Iterable[str] | None = None) -> None:
        """Break the live graph of these series now (e.g. while a device calibrates)."""
        self._live.invalidate(keys)

    def _fail(self, key: str, message: str) -> None:
        with self._lock:
            health = self._health[self._key(key)]
            health.failures += 1
            health.last_error = message
            health.last_error_epoch = time.time()
        print(f"[{self.plugin_key}] {message}")

    def _key(self, key: str) -> str:
        self._contract(key)
        return key

    # ----------------------------------------------------------- status

    def reset(self) -> None:
        """Empty the live view and the counters; outlets stay open."""
        with self._lock:
            for key, health in self._health.items():
                self._health[key] = _Health(open=health.open)
        self._live.reset()

    def status_blocks(self) -> dict[str, Any]:
        """``live`` and ``stream_health``, merged into the plugin's status by the core."""
        with self._lock:
            streams = {key: health.public() for key, health in self._health.items()}
            errors = [
                (health.last_error_epoch or 0.0, health.last_error)
                for health in self._health.values()
                if health.failures
            ]
        return {
            "live": self._live.snapshot(),
            "stream_health": {
                "failed": bool(errors),
                "last_error": max(errors)[1] if errors else "",
                "streams": streams,
            },
        }


def _number(value: Any) -> float:
    """A channel value; anything missing or not a number is NaN, never 0."""
    if value is None or value == "":
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan
