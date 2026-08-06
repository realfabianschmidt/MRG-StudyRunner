"""LSL ingestion and slowest-grid backup recording for the detached worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from study_runner.recording.backup import BackupProjection, BackupSampler, projections_from_manifest

from .core import NativeXdfCore, NativeXdfWriter


BOUNDARY_INTERVAL_SECONDS = 10.0
DURABLE_FLUSH_INTERVAL_SECONDS = 5.0
RESOLVE_TIMEOUT_SECONDS = 0.5
PULL_TIMEOUT_SECONDS = 0.25
CLOCK_OFFSET_INTERVAL_SECONDS = 5.0
DRAIN_GRACE_SECONDS = 0.35
DRAIN_STOP_JOIN_TIMEOUT_SECONDS = 0.65
ABORT_JOIN_TIMEOUT_SECONDS = 0.25

SUPPORTED_FORMATS = frozenset(
    {"int8", "int16", "int32", "int64", "float32", "double64", "float64", "string"}
)


def require_pylsl() -> Any:
    try:
        import pylsl
    except Exception as error:
        raise RuntimeError(f"pylsl/liblsl is unavailable: {error}") from error
    required = (
        "resolve_byprop",
        "StreamInlet",
        "local_clock",
        "cf_float32",
        "cf_double64",
        "cf_string",
        "cf_int8",
        "cf_int16",
        "cf_int32",
        "cf_int64",
    )
    missing = [name for name in required if not hasattr(pylsl, name)]
    if missing:
        raise RuntimeError(f"pylsl is missing required APIs: {', '.join(missing)}")
    return pylsl


def lsl_version_info(pylsl_module: Any) -> dict[str, Any]:
    """Best-effort package/native version evidence; probing never blocks recording."""

    package_version = str(getattr(pylsl_module, "__version__", "") or "")
    if not package_version:
        try:
            package_version = importlib_metadata.version("pylsl")
        except Exception:
            package_version = "unknown"
    native_version: int | str | None = None
    probe_error: str | None = None
    try:
        probe = getattr(pylsl_module, "library_version", None)
        native_version = probe() if callable(probe) else None
    except Exception as error:
        probe_error = f"{type(error).__name__}: {error}"
    return {
        "pylsl_package_version": package_version,
        "liblsl_library_version": native_version,
        "version_probe_error": probe_error,
    }


@dataclass(frozen=True)
class StreamSpec:
    key: str
    source_id: str
    stream_type: str
    nominal_rate_hz: float
    channel_format: str
    channels: tuple[str, ...]
    channel_units: tuple[str, ...]
    sequence_channel: str | None = None
    primary: bool = False

    @classmethod
    def from_manifest(cls, payload: Mapping[str, Any]) -> "StreamSpec":
        key = str(payload.get("key") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        stream_type = str(payload.get("type") or "").strip()
        channel_format = str(payload.get("channel_format") or "").strip()
        channels = tuple(str(value).strip() for value in payload.get("channels") or [])
        units = tuple(str(value).strip() for value in payload.get("channel_units") or [])
        try:
            rate = float(payload.get("nominal_rate_hz"))
        except (TypeError, ValueError) as error:
            raise ValueError("stream nominal_rate_hz must be numeric") from error
        if not key or not source_id or not stream_type:
            raise ValueError("stream key, source_id and type are required")
        if channel_format not in SUPPORTED_FORMATS:
            raise ValueError(f"unsupported stream channel_format: {channel_format}")
        if not channels or len(set(channels)) != len(channels):
            raise ValueError("stream channels must be unique and non-empty")
        if units and len(units) != len(channels):
            raise ValueError("stream channel_units must match channels")
        if not math.isfinite(rate) or rate < 0:
            raise ValueError("stream nominal_rate_hz must be finite and non-negative")
        sequence = str(payload.get("sequence_channel") or "").strip() or None
        if sequence is not None and sequence not in channels:
            raise ValueError("sequence_channel is not present in stream channels")
        return cls(
            key=key,
            source_id=source_id,
            stream_type=stream_type,
            nominal_rate_hz=rate,
            channel_format=channel_format,
            channels=channels,
            channel_units=units or tuple("" for _ in channels),
            sequence_channel=sequence,
            primary=bool(payload.get("primary", False)),
        )


@dataclass(frozen=True)
class CachedStreamSample:
    values: Mapping[str, Any]
    received_monotonic: float
    source_timestamp: float | None
    sequence: int | None
    source_ok: bool


class ProjectionCache:
    """Thread-safe latest-value cache; no hardware is polled at backup deadlines."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[tuple[str, str], CachedStreamSample] = {}

    def update(
        self,
        plugin_key: str,
        spec: StreamSpec,
        row: Sequence[Any],
        *,
        received_monotonic: float,
        source_timestamp: float | None,
        fallback_sequence: int,
    ) -> None:
        values = dict(zip(spec.channels, row, strict=True))
        sequence: int | None = fallback_sequence
        if spec.sequence_channel:
            try:
                sequence = int(float(values[spec.sequence_channel]))
            except (TypeError, ValueError, OverflowError):
                sequence = None
        with self._lock:
            self._values[(plugin_key, spec.key)] = CachedStreamSample(
                values=values,
                received_monotonic=float(received_monotonic),
                source_timestamp=float(source_timestamp) if source_timestamp is not None else None,
                sequence=sequence,
                source_ok=True,
            )

    def get(self, plugin_key: str, stream_key: str) -> CachedStreamSample | None:
        with self._lock:
            return self._values.get((plugin_key, stream_key))

    def mark_degraded(self, plugin_key: str, stream_key: str) -> bool:
        """Mark only an existing real sample degraded; never invent values."""

        with self._lock:
            key = (plugin_key, stream_key)
            current = self._values.get(key)
            if current is None:
                return False
            self._values[key] = CachedStreamSample(
                values=current.values,
                received_monotonic=current.received_monotonic,
                source_timestamp=current.source_timestamp,
                sequence=current.sequence,
                source_ok=False,
            )
            return True


@dataclass
class StreamRuntimeState:
    spec: StreamSpec
    stream_id: int
    header_written: bool = False
    footer_written: bool = False
    connected: bool = False
    sample_count: int = 0
    first_timestamp: float | None = None
    last_timestamp: float | None = None
    clock_offsets: list[tuple[float, float]] = field(default_factory=list)
    reconnect_count: int = 0
    last_error: str | None = None
    last_clock_error: str | None = None
    last_sample_monotonic: float | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "key": self.spec.key,
            "source_id": self.spec.source_id,
            "nominal_rate_hz": self.spec.nominal_rate_hz,
            "primary": self.spec.primary,
            "stream_id": self.stream_id,
            "header_written": self.header_written,
            "footer_written": self.footer_written,
            "connected": self.connected,
            "sample_count": self.sample_count,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "last_clock_error": self.last_clock_error,
            "last_sample_age_seconds": (
                max(0.0, time.monotonic() - self.last_sample_monotonic)
                if self.last_sample_monotonic is not None
                else None
            ),
        }


class LslSourceRecorder:
    """One append-never plugin XDF containing all manifest-declared LSL streams."""

    def __init__(
        self,
        core: NativeXdfCore,
        *,
        plugin_key: str,
        target_path: Path,
        streams: Sequence[Mapping[str, Any]],
        cache: ProjectionCache,
        pylsl_module: Any,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not plugin_key.strip():
            raise ValueError("plugin_key is required")
        specs = tuple(StreamSpec.from_manifest(item) for item in streams)
        if not specs:
            raise ValueError("recording source declares no LSL streams")
        source_ids = [item.source_id for item in specs]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("recording source contains duplicate source_id values")
        primary_indexes = [index for index, item in enumerate(specs) if item.primary]
        if len(primary_indexes) > 1:
            raise ValueError("recording source declares more than one primary stream")
        self.plugin_key = plugin_key
        self.target_path = Path(target_path).resolve()
        self._writer = core.create_writer(self.target_path)
        self._cache = cache
        self._pylsl = pylsl_module
        self._clock = clock
        self._stop = threading.Event()
        self._drain_requested = threading.Event()
        self._drain_until_monotonic = 0.0
        self._lock = threading.RLock()
        self._states = [StreamRuntimeState(spec=spec, stream_id=index + 1) for index, spec in enumerate(specs)]
        self._primary_index = primary_indexes[0] if primary_indexes else 0
        self._threads: list[threading.Thread] = []
        self._checkpoint_thread: threading.Thread | None = None
        self._closed = False
        self._fatal_error: str | None = None
        self._readiness = threading.Condition(self._lock)

    def start(self) -> None:
        for state in self._states:
            thread = threading.Thread(
                target=self._record_stream,
                args=(state,),
                name=f"xdf-{self.plugin_key}-{state.spec.key}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()
        self._checkpoint_thread = threading.Thread(
            target=self._checkpoint_loop,
            name=f"xdf-checkpoint-{self.plugin_key}",
            daemon=True,
        )
        self._checkpoint_thread.start()

    def _record_stream(self, state: StreamRuntimeState) -> None:
        inlet: Any | None = None
        sequence = 0
        next_clock_offset = self._clock()
        while not self._stop.is_set():
            try:
                if inlet is None:
                    # Freeze drains only inlets which were already connected.
                    # Reconnecting here could append unrelated later samples
                    # and make the end-marker boundary non-deterministic.
                    if self._drain_requested.is_set():
                        break
                    inlet = self._connect(state)
                    if inlet is None:
                        continue
                    next_clock_offset = self._clock()
                samples, timestamps = inlet.pull_chunk(
                    timeout=PULL_TIMEOUT_SECONDS,
                    max_samples=1024,
                )
                if samples:
                    if len(samples) != len(timestamps):
                        raise RuntimeError("LSL returned mismatched samples and timestamps")
                    rows = [tuple(row) for row in samples]
                    received = self._clock()
                    self._writer.write_samples(
                        state.stream_id,
                        timestamps,
                        rows,
                        channel_format=state.spec.channel_format,
                        channel_count=len(state.spec.channels),
                    )
                    with self._lock:
                        for row, timestamp in zip(rows, timestamps, strict=True):
                            if len(row) != len(state.spec.channels):
                                raise RuntimeError("LSL sample channel count changed")
                            sequence += 1
                            self._cache.update(
                                self.plugin_key,
                                state.spec,
                                row,
                                received_monotonic=received,
                                source_timestamp=float(timestamp),
                                fallback_sequence=sequence,
                            )
                        state.sample_count += len(rows)
                        state.first_timestamp = state.first_timestamp or float(timestamps[0])
                        state.last_timestamp = float(timestamps[-1])
                        state.last_sample_monotonic = received
                        state.last_error = None
                        self._readiness.notify_all()
                    if (
                        self._drain_requested.is_set()
                        and time.monotonic() >= self._drain_until_monotonic
                    ):
                        break
                elif self._drain_requested.is_set():
                    # One quiet bounded pull after the freeze request is the
                    # tail barrier: samples queued just behind the end marker
                    # are retained, then the inlet is closed without reconnect.
                    break
                if self._drain_requested.is_set():
                    continue
                now = self._clock()
                if now >= next_clock_offset:
                    try:
                        correction = float(inlet.time_correction(timeout=0.1))
                        local_time = float(self._pylsl.local_clock())
                        self._writer.write_clock_offset(state.stream_id, local_time, correction)
                        with self._lock:
                            state.clock_offsets.append((local_time - correction, correction))
                            state.last_clock_error = None
                    except Exception as error:
                        # Clock-offset diagnostics may be temporarily
                        # unavailable while sample transport remains healthy.
                        with self._lock:
                            state.last_clock_error = f"{type(error).__name__}: {error}"
                    next_clock_offset = now + CLOCK_OFFSET_INTERVAL_SECONDS
            except Exception as error:
                self._cache.mark_degraded(self.plugin_key, state.spec.key)
                with self._lock:
                    state.connected = False
                    state.last_error = f"{type(error).__name__}: {error}"
                    state.reconnect_count += 1
                if inlet is not None:
                    try:
                        inlet.close_stream()
                    except Exception:
                        pass
                inlet = None
                if self._drain_requested.is_set():
                    break
                if self._stop.wait(0.25):
                    break
        if inlet is not None:
            try:
                inlet.close_stream()
            except Exception:
                pass
        with self._lock:
            state.connected = False

    def _connect(self, state: StreamRuntimeState) -> Any | None:
        matches = self._pylsl.resolve_byprop(
            "source_id",
            state.spec.source_id,
            minimum=1,
            timeout=RESOLVE_TIMEOUT_SECONDS,
        )
        if self._stop.is_set() or self._drain_requested.is_set() or not matches:
            return None
        if len(matches) != 1:
            raise RuntimeError(f"source_id {state.spec.source_id!r} is not unique")
        info = matches[0]
        inlet = self._pylsl.StreamInlet(
            info,
            max_buflen=360,
            max_chunklen=0,
            recover=True,
            processing_flags=0,
        )
        try:
            inlet.open_stream(timeout=RESOLVE_TIMEOUT_SECONDS)
            # Resolver results may contain only a short StreamInfo without
            # channel descriptions. Validate the full inlet metadata before
            # committing its XDF header.
            info_reader = getattr(inlet, "info", None)
            full_info = (
                info_reader(timeout=RESOLVE_TIMEOUT_SECONDS)
                if callable(info_reader)
                else info
            )
            header_xml = self._validate_info(state.spec, full_info)
        except Exception:
            try:
                inlet.close_stream()
            except Exception:
                pass
            raise
        with self._lock:
            if not state.header_written:
                self._writer.write_stream_header(state.stream_id, header_xml)
                state.header_written = True
            state.connected = True
            state.last_error = None
            self._readiness.notify_all()
        return inlet

    def _validate_info(self, spec: StreamSpec, info: Any) -> str:
        if str(info.source_id()) != spec.source_id:
            raise RuntimeError("resolved LSL source_id does not match the manifest")
        if str(info.type()) != spec.stream_type:
            raise RuntimeError("resolved LSL type does not match the manifest")
        if int(info.channel_count()) != len(spec.channels):
            raise RuntimeError("resolved LSL channel_count does not match the manifest")
        actual_rate = float(info.nominal_srate())
        tolerance = max(1e-9, spec.nominal_rate_hz * 1e-6)
        if abs(actual_rate - spec.nominal_rate_hz) > tolerance:
            raise RuntimeError("resolved LSL nominal_srate does not match the manifest")
        expected_code = {
            "float32": self._pylsl.cf_float32,
            "double64": self._pylsl.cf_double64,
            "float64": self._pylsl.cf_double64,
            "string": self._pylsl.cf_string,
            "int8": self._pylsl.cf_int8,
            "int16": self._pylsl.cf_int16,
            "int32": self._pylsl.cf_int32,
            "int64": self._pylsl.cf_int64,
        }[spec.channel_format]
        if int(info.channel_format()) != int(expected_code):
            raise RuntimeError("resolved LSL channel_format does not match the manifest")
        raw_xml = info.as_xml()
        if isinstance(raw_xml, bytes):
            raw_xml = raw_xml.decode("utf-8")
        try:
            root = ElementTree.fromstring(str(raw_xml))
        except (ElementTree.ParseError, UnicodeError) as error:
            raise RuntimeError(f"resolved LSL StreamInfo XML is invalid: {error}") from error
        channel_nodes = root.findall("./desc/channels/channel")
        labels = tuple((node.findtext("label") or "").strip() for node in channel_nodes)
        units = tuple((node.findtext("unit") or "").strip() for node in channel_nodes)
        if labels != spec.channels:
            raise RuntimeError(
                f"resolved LSL channel labels/order do not match the manifest: {labels!r}"
            )
        if units != spec.channel_units:
            raise RuntimeError(
                f"resolved LSL channel units/order do not match the manifest: {units!r}"
            )
        return str(raw_xml)

    def _checkpoint_loop(self) -> None:
        next_boundary = self._clock() + BOUNDARY_INTERVAL_SECONDS
        next_flush = self._clock() + DURABLE_FLUSH_INTERVAL_SECONDS
        while not self._stop.is_set():
            try:
                now = self._clock()
                if now >= next_boundary:
                    self._writer.boundary()
                    next_boundary += BOUNDARY_INTERVAL_SECONDS
                if now >= next_flush:
                    self._writer.flush(durable=True)
                    next_flush += DURABLE_FLUSH_INTERVAL_SECONDS
            except Exception as error:
                with self._lock:
                    self._fatal_error = f"checkpoint failed: {type(error).__name__}: {error}"
                self._stop.set()
                return
            self._stop.wait(max(0.01, min(next_boundary, next_flush) - self._clock()))

    def wait_until_ready(
        self,
        *,
        require_stream_headers: bool,
        require_fresh_primary_sample: bool,
        timeout_seconds: float,
        maximum_sample_age_seconds: float,
    ) -> bool:
        """Wait boundedly for headers and, for sensors, one fresh primary sample."""

        deadline = self._clock() + max(0.0, float(timeout_seconds))
        with self._readiness:
            while True:
                now = self._clock()
                headers_ready = not require_stream_headers or all(
                    state.header_written for state in self._states
                )
                primary = self._states[self._primary_index]
                primary_ready = not require_fresh_primary_sample or (
                    primary.sample_count >= 1
                    and primary.last_sample_monotonic is not None
                    and now - primary.last_sample_monotonic <= maximum_sample_age_seconds
                )
                if headers_ready and primary_ready:
                    return True
                remaining = deadline - now
                if remaining <= 0 or self._fatal_error:
                    return False
                self._readiness.wait(timeout=min(0.1, remaining))

    def freeze(self, *, reason: str) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return self.status()
            self._drain_until_monotonic = time.monotonic() + DRAIN_GRACE_SECONDS
            self._drain_requested.set()
        grace_deadline = self._drain_until_monotonic
        for thread in self._threads:
            thread.join(timeout=max(0.0, grace_deadline - time.monotonic()))
        # Continuous streams may never produce a quiet pull. The grace
        # deadline defines their deterministic cutover; stop then gets a
        # second bounded join window before any native writer is touched.
        self._stop.set()
        stop_deadline = time.monotonic() + DRAIN_STOP_JOIN_TIMEOUT_SECONDS
        for thread in self._threads:
            thread.join(timeout=max(0.0, stop_deadline - time.monotonic()))
        if self._checkpoint_thread is not None:
            self._checkpoint_thread.join(timeout=max(0.0, stop_deadline - time.monotonic()))
        alive = [thread.name for thread in self._threads if thread.is_alive()]
        if self._checkpoint_thread is not None and self._checkpoint_thread.is_alive():
            alive.append(self._checkpoint_thread.name)
        if alive:
            with self._lock:
                self._fatal_error = f"LSL recorder drain timed out: {', '.join(alive)}"
            raise RuntimeError(self._fatal_error)
        try:
            with self._lock:
                for state in self._states:
                    if not state.header_written or state.footer_written:
                        continue
                    self._writer.write_stream_footer(state.stream_id, self._footer_xml(state, reason))
                    state.footer_written = True
                self._writer.boundary()
                self._writer.close(durable=True)
                self._closed = True
        finally:
            if self._closed:
                self._writer.destroy()
        return self.status()

    @staticmethod
    def _footer_xml(state: StreamRuntimeState, reason: str) -> str:
        first = state.first_timestamp or 0.0
        last = state.last_timestamp or 0.0
        offsets = "".join(
            f"<offset><time>{collection:.17g}</time><value>{value:.17g}</value></offset>"
            for collection, value in state.clock_offsets
        )
        return (
            "<?xml version=\"1.0\"?><info>"
            f"<first_timestamp>{first:.17g}</first_timestamp>"
            f"<last_timestamp>{last:.17g}</last_timestamp>"
            f"<sample_count>{state.sample_count}</sample_count>"
            f"<clock_offsets>{offsets}</clock_offsets>"
            f"<study_runner_close_reason>{escape(reason)}</study_runner_close_reason>"
            f"<reconnect_count>{state.reconnect_count}</reconnect_count>"
            "</info>"
        )

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "plugin_key": self.plugin_key,
                "target_path": str(self.target_path),
                "closed": self._closed,
                "fatal_error": self._fatal_error,
                "streams": [state.public_dict() for state in self._states],
            }

    def abort(self) -> None:
        """Stop ingestion and retain the footer-less fragment for recovery."""

        self._drain_requested.set()
        self._stop.set()
        deadline = time.monotonic() + ABORT_JOIN_TIMEOUT_SECONDS
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if self._checkpoint_thread is not None:
            self._checkpoint_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = [thread.name for thread in self._threads if thread.is_alive()]
        if alive:
            with self._lock:
                self._fatal_error = f"LSL recorder abort timed out: {', '.join(alive)}"
            # Never destroy a native writer while a blocked inlet thread may
            # still return and access it. The worker process owns final cleanup.
            raise RuntimeError(self._fatal_error)
        try:
            self._writer.abort(durable=True)
        finally:
            self._writer.destroy()
            with self._lock:
                self._closed = True


class BackupRecorder:
    """Derived, explicitly labelled slowest-grid XDF using the latest cache only."""

    def __init__(
        self,
        core: NativeXdfCore,
        *,
        target_path: Path,
        payload: Mapping[str, Any],
        cache: ProjectionCache,
        pylsl_module: Any,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        raw_projections = payload.get("projections")
        if not isinstance(raw_projections, list) or not raw_projections:
            raise ValueError("backup projections are required")
        projections: list[BackupProjection] = []
        for raw in raw_projections:
            if not isinstance(raw, Mapping):
                raise ValueError("backup projection must be an object")
            projections.extend(projections_from_manifest(str(raw.get("plugin_key") or ""), raw))
        anchor_epoch = float(payload.get("grid_anchor_epoch"))
        if not math.isfinite(anchor_epoch):
            raise ValueError("backup grid_anchor_epoch must be finite")
        start_monotonic = monotonic() - max(0.0, wall_clock() - anchor_epoch)
        self._sampler = BackupSampler(tuple(projections), start_monotonic=start_monotonic)
        expected_rate = float(payload.get("rate_hz"))
        if abs(self._sampler.rate_hz - expected_rate) > 1e-9:
            raise ValueError("backup rate does not match projection minimum")
        expected_names = tuple(str(value) for value in payload.get("channel_names") or [])
        if expected_names != self._sampler.channel_names:
            raise ValueError("backup channel order does not match its projections")
        self.target_path = Path(target_path).resolve()
        self._writer = core.create_writer(self.target_path)
        self._cache = cache
        self._pylsl = pylsl_module
        self._monotonic = monotonic
        self._payload = dict(payload)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._sample_count = 0
        self._first_timestamp: float | None = None
        self._last_timestamp: float | None = None
        self._last_error: str | None = None
        self._last_boundary = monotonic()
        self._last_flush = monotonic()
        self._writer.write_stream_header(1, self._header_xml())

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="xdf-derived-backup", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                for projection in self._sampler.projections:
                    cached = self._cache.get(projection.plugin_key, projection.stream_id)
                    if cached is None:
                        continue
                    projected = {
                        channel.source_channel: cached.values[channel.source_channel]
                        for channel in projection.channels
                    }
                    self._sampler.update(
                        projection.plugin_key,
                        projection.stream_id,
                        projected,
                        received_monotonic=cached.received_monotonic,
                        source_timestamp=cached.source_timestamp,
                        sequence=cached.sequence,
                        source_ok=cached.source_ok,
                    )
                now = self._monotonic()
                frames = self._sampler.emit_due(now)
                if frames:
                    clock_delta = float(self._pylsl.local_clock()) - now
                    timestamps = [frame.deadline_monotonic + clock_delta for frame in frames]
                    rows = [
                        tuple(frame.values[name] for name in self._sampler.channel_names)
                        for frame in frames
                    ]
                    self._writer.write_samples(
                        1,
                        timestamps,
                        rows,
                        channel_format="double64",
                        channel_count=len(self._sampler.channel_names),
                    )
                    with self._lock:
                        self._sample_count += len(frames)
                        self._first_timestamp = self._first_timestamp or timestamps[0]
                        self._last_timestamp = timestamps[-1]
                if now - self._last_boundary >= BOUNDARY_INTERVAL_SECONDS:
                    self._writer.boundary()
                    self._last_boundary = now
                if now - self._last_flush >= DURABLE_FLUSH_INTERVAL_SECONDS:
                    self._writer.flush(durable=True)
                    self._last_flush = now
                delay = max(0.01, min(0.1, self._sampler.next_deadline - self._monotonic()))
                self._stop.wait(delay)
        except Exception as error:
            with self._lock:
                self._last_error = f"{type(error).__name__}: {error}"
            self._stop.set()

    def _header_xml(self) -> str:
        labels = "".join(
            f"<channel><label>{escape(name)}</label><unit>derived_qc</unit></channel>"
            for name in self._sampler.channel_names
        )
        active_plugins = json.dumps(
            list(self._payload.get("active_plugins") or []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        source_rates = json.dumps(
            self._payload.get("source_rates_hz") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        strategy = str(self._payload.get("resampling_strategy") or "")
        sampler_metadata = self._sampler.stream_metadata()
        status_codes = json.dumps(
            sampler_metadata["status_codes"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        projection_rules = json.dumps(
            sampler_metadata["projections"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            "<?xml version=\"1.0\"?><info>"
            "<name>StudyRunnerBackup</name><type>DerivedBackup</type>"
            f"<channel_count>{len(self._sampler.channel_names)}</channel_count>"
            f"<nominal_srate>{self._sampler.rate_hz:.17g}</nominal_srate>"
            "<channel_format>double64</channel_format>"
            "<source_id>study_runner.derived_backup</source_id>"
            "<version>1.100000</version><created_at>0</created_at>"
            "<uid>study_runner.derived_backup</uid><session_id>study_runner</session_id>"
            "<hostname>localhost</hostname><desc>"
            f"<channels>{labels}</channels>"
            "<artifact_role>derived_backup</artifact_role>"
            f"<resampling_strategy>{escape(strategy)}</resampling_strategy>"
            f"<active_plugins>{escape(active_plugins)}</active_plugins>"
            f"<source_rates_hz>{escape(source_rates)}</source_rates_hz>"
            f"<status_codes>{escape(status_codes)}</status_codes>"
            f"<projection_rules>{escape(projection_rules)}</projection_rules>"
            "<staleness_rule>future_or_age_above_projection_stale_after_seconds_to_nan;"
            "valid=0;status=stale</staleness_rule>"
            "<degraded_rule>retain_last_real_values_only_within_freshness_window;"
            "valid=0;status=degraded</degraded_rule>"
            "<missing_rule>value_channels=NaN;valid=0;status=missing</missing_rule>"
            "<invalid_values>NaN</invalid_values>"
            "</desc></info>"
        )

    def freeze(self, *, reason: str) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return self.status()
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                raise RuntimeError("backup recorder thread did not drain")
        try:
            with self._lock:
                first = self._first_timestamp or 0.0
                last = self._last_timestamp or 0.0
                footer = (
                    "<?xml version=\"1.0\"?><info>"
                    f"<first_timestamp>{first:.17g}</first_timestamp>"
                    f"<last_timestamp>{last:.17g}</last_timestamp>"
                    f"<sample_count>{self._sample_count}</sample_count>"
                    "<clock_offsets/>"
                    f"<study_runner_close_reason>{escape(reason)}</study_runner_close_reason>"
                    "</info>"
                )
                self._writer.write_stream_footer(1, footer)
                self._writer.boundary()
                self._writer.close(durable=True)
                self._closed = True
        finally:
            if self._closed:
                self._writer.destroy()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "target_path": str(self.target_path),
                "closed": self._closed,
                "sample_count": self._sample_count,
                "first_timestamp": self._first_timestamp,
                "last_timestamp": self._last_timestamp,
                "last_error": self._last_error,
                "rate_hz": self._sampler.rate_hz,
                "channel_names": list(self._sampler.channel_names),
            }

    def abort(self) -> None:
        """Stop the grid and retain the footer-less backup fragment."""

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            self._writer.abort(durable=True)
        finally:
            self._writer.destroy()
            with self._lock:
                self._closed = True
