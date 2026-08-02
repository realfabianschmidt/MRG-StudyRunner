"""Memory-safe ctypes boundary for the small, versioned native XDF core."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import json
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping, Sequence


EXPECTED_ABI_VERSION = 1
REQUIRED_CANONICAL_FEATURES = frozenset(
    {
        "typed_batches",
        "string_batches",
        "clock_offsets",
        "boundaries",
        "exclusive_create",
        "durable_flush",
        "checked_raw_chunks",
        "lossless_merge",
    }
)

VALUE_FORMATS = {
    "int8": (1, ctypes.c_int8),
    "int16": (2, ctypes.c_int16),
    "int32": (3, ctypes.c_int32),
    "int64": (4, ctypes.c_int64),
    "float32": (5, ctypes.c_float),
    "double64": (6, ctypes.c_double),
    "float64": (6, ctypes.c_double),
}
INTEGER_LIMITS = {
    "int8": (-(2**7), 2**7 - 1),
    "int16": (-(2**15), 2**15 - 1),
    "int32": (-(2**31), 2**31 - 1),
    "int64": (-(2**63), 2**63 - 1),
}


class NativeXdfError(RuntimeError):
    """The native core rejected a command or could not preserve XDF semantics."""

    def __init__(self, operation: str, status: int, message: str) -> None:
        super().__init__(f"{operation} failed ({status}): {message or 'native core error'}")
        self.operation = operation
        self.status = int(status)
        self.native_message = message


@dataclass(frozen=True)
class CoreProbe:
    path: Path
    abi_version: int
    canonical_xdf: bool
    implementation: str
    upstream_version: str
    byte_order: str
    features: Mapping[str, bool]

    @property
    def missing_features(self) -> tuple[str, ...]:
        return tuple(sorted(name for name in REQUIRED_CANONICAL_FEATURES if not self.features.get(name)))

    @property
    def usable(self) -> bool:
        return (
            self.abi_version == EXPECTED_ABI_VERSION
            and self.canonical_xdf
            and self.byte_order == "little"
            and not self.missing_features
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "abi_version": self.abi_version,
            "canonical_xdf": self.canonical_xdf,
            "implementation": self.implementation,
            "upstream_version": self.upstream_version,
            "byte_order": self.byte_order,
            "features": dict(self.features),
            "missing_features": list(self.missing_features),
            "usable": self.usable,
        }


class _MergeReport(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("source_count", ctypes.c_uint32),
        ("stream_count", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("copied_chunk_count", ctypes.c_uint64),
        ("copied_payload_bytes", ctypes.c_uint64),
    ]


def _load_library(path: Path) -> ctypes.CDLL:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise NativeXdfError("load", 2, f"native XDF core does not exist: {target}")
    try:
        return ctypes.CDLL(str(target))
    except OSError as error:
        raise NativeXdfError("load", 2, str(error)) from error


def _bind_probe(library: ctypes.CDLL) -> None:
    library.sr_xdf_core_abi_version.argtypes = []
    library.sr_xdf_core_abi_version.restype = ctypes.c_uint32
    library.sr_xdf_core_probe_json.argtypes = []
    library.sr_xdf_core_probe_json.restype = ctypes.c_char_p
    library.sr_xdf_copy_last_error.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    library.sr_xdf_copy_last_error.restype = ctypes.c_uint64


def probe_core_library(path: Path) -> CoreProbe:
    """Load and validate probe data without constructing a writer."""

    target = Path(path).expanduser().resolve()
    library = _load_library(target)
    try:
        _bind_probe(library)
        abi_version = int(library.sr_xdf_core_abi_version())
        raw_probe = library.sr_xdf_core_probe_json()
        if not raw_probe:
            raise NativeXdfError("probe", 255, "native core returned an empty probe")
        payload = json.loads(raw_probe.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError, TypeError) as error:
        raise NativeXdfError("probe", 255, f"invalid native core ABI/probe: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), dict):
        raise NativeXdfError("probe", 255, "native core probe has an invalid schema")
    if int(payload.get("abi_version") or 0) != abi_version:
        raise NativeXdfError("probe", 255, "native core ABI function/probe disagree")
    return CoreProbe(
        path=target,
        abi_version=abi_version,
        canonical_xdf=bool(payload.get("canonical_xdf", False)),
        implementation=str(payload.get("implementation") or ""),
        upstream_version=str(payload.get("upstream_version") or ""),
        byte_order=str(payload.get("byte_order") or ""),
        features={str(key): bool(value) for key, value in payload["features"].items()},
    )


class NativeXdfCore:
    """Bound native core. Calls either succeed canonically or fail explicitly."""

    def __init__(self, path: Path, *, require_canonical: bool = True) -> None:
        self.path = Path(path).expanduser().resolve()
        self.probe = probe_core_library(self.path)
        if require_canonical and not self.probe.usable:
            details = ", ".join(self.probe.missing_features) or "ABI/byte-order/canonical flag"
            raise NativeXdfError("probe", 4, f"native core is not canonical: {details}")
        self._library = _load_library(self.path)
        self._bind()

    def _bind(self) -> None:
        library = self._library
        _bind_probe(library)
        library.sr_xdf_writer_open_exclusive.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.sr_xdf_writer_open_exclusive.restype = ctypes.c_int
        for name in ("sr_xdf_writer_write_stream_header", "sr_xdf_writer_write_stream_footer"):
            function = getattr(library, name)
            function.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint64]
            function.restype = ctypes.c_int
        library.sr_xdf_writer_write_numeric_samples.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        library.sr_xdf_writer_write_numeric_samples.restype = ctypes.c_int
        library.sr_xdf_writer_write_string_samples.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.c_uint64,
            ctypes.c_uint32,
        ]
        library.sr_xdf_writer_write_string_samples.restype = ctypes.c_int
        library.sr_xdf_writer_write_clock_offset.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_double,
            ctypes.c_double,
        ]
        library.sr_xdf_writer_write_clock_offset.restype = ctypes.c_int
        library.sr_xdf_writer_write_boundary.argtypes = [ctypes.c_void_p]
        library.sr_xdf_writer_write_boundary.restype = ctypes.c_int
        library.sr_xdf_writer_flush.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.sr_xdf_writer_flush.restype = ctypes.c_int
        library.sr_xdf_writer_close.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.sr_xdf_writer_close.restype = ctypes.c_int
        library.sr_xdf_writer_abort.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.sr_xdf_writer_abort.restype = ctypes.c_int
        library.sr_xdf_writer_destroy.argtypes = [ctypes.c_void_p]
        library.sr_xdf_writer_destroy.restype = None
        library.sr_xdf_merge_files.argtypes = [
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_uint64,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(_MergeReport),
        ]
        library.sr_xdf_merge_files.restype = ctypes.c_int

    def _error(self) -> str:
        required = int(self._library.sr_xdf_copy_last_error(None, 0))
        buffer = ctypes.create_string_buffer(required + 1)
        self._library.sr_xdf_copy_last_error(buffer, len(buffer))
        return buffer.value.decode("utf-8", errors="replace")

    def _check(self, status: int, operation: str) -> None:
        if int(status) != 0:
            raise NativeXdfError(operation, int(status), self._error())

    def create_writer(self, path: Path) -> "NativeXdfWriter":
        return NativeXdfWriter(self, Path(path))

    def merge(
        self,
        sources: Sequence[tuple[str, Path]],
        output: Path,
        *,
        durable: bool = True,
    ) -> dict[str, int]:
        if not sources:
            raise ValueError("at least one XDF source is required")
        encoded_paths = [str(Path(path).resolve()).encode("utf-8") for _, path in sources]
        encoded_keys = [str(key).encode("utf-8") for key, _ in sources]
        path_array = (ctypes.c_char_p * len(sources))(*encoded_paths)
        key_array = (ctypes.c_char_p * len(sources))(*encoded_keys)
        report = _MergeReport()
        status = self._library.sr_xdf_merge_files(
            path_array,
            key_array,
            len(sources),
            str(Path(output).resolve()).encode("utf-8"),
            int(bool(durable)),
            ctypes.byref(report),
        )
        self._check(status, "merge")
        if report.abi_version != EXPECTED_ABI_VERSION:
            raise NativeXdfError("merge", 255, "merge report ABI mismatch")
        return {
            "source_count": int(report.source_count),
            "stream_count": int(report.stream_count),
            "copied_chunk_count": int(report.copied_chunk_count),
            "copied_payload_bytes": int(report.copied_payload_bytes),
        }


class NativeXdfWriter:
    """One exclusive-create XDF output. Footer completeness is enforced natively."""

    def __init__(self, core: NativeXdfCore, path: Path) -> None:
        self.core = core
        self.path = Path(path).resolve()
        self._handle = ctypes.c_void_p()
        self._lock = threading.RLock()
        self._closed = False
        status = core._library.sr_xdf_writer_open_exclusive(
            str(self.path).encode("utf-8"), ctypes.byref(self._handle)
        )
        core._check(status, "writer_open_exclusive")
        if not self._handle.value:
            raise NativeXdfError("writer_open_exclusive", 255, "native core returned a null handle")

    def _require_open(self) -> ctypes.c_void_p:
        if self._closed or not self._handle.value:
            raise NativeXdfError("writer", 3, "writer is closed")
        return self._handle

    @staticmethod
    def _buffer(value: bytes) -> tuple[ctypes.Array[ctypes.c_char], ctypes.c_void_p]:
        buffer = ctypes.create_string_buffer(value, len(value)) if value else ctypes.create_string_buffer(1)
        return buffer, ctypes.cast(buffer, ctypes.c_void_p)

    def write_stream_header(self, stream_id: int, xml: str) -> None:
        self._write_xml("sr_xdf_writer_write_stream_header", stream_id, xml)

    def write_stream_footer(self, stream_id: int, xml: str) -> None:
        self._write_xml("sr_xdf_writer_write_stream_footer", stream_id, xml)

    def _write_xml(self, function_name: str, stream_id: int, xml: str) -> None:
        encoded = xml.encode("utf-8")
        buffer, pointer = self._buffer(encoded)
        with self._lock:
            handle = self._require_open()
            status = getattr(self.core._library, function_name)(handle, int(stream_id), pointer, len(encoded))
            self.core._check(status, function_name)
            del buffer

    def write_samples(
        self,
        stream_id: int,
        timestamps: Sequence[float],
        samples: Sequence[Sequence[Any]],
        *,
        channel_format: str,
        channel_count: int,
    ) -> None:
        if len(timestamps) != len(samples):
            raise ValueError("timestamp/sample count mismatch")
        if channel_count < 1:
            raise ValueError("channel_count must be positive")
        flat: list[Any] = []
        for row in samples:
            if len(row) != channel_count:
                raise ValueError("sample channel count mismatch")
            flat.extend(row)
        timestamps_array = (ctypes.c_double * len(timestamps))(
            *(float(value) for value in timestamps)
        )
        with self._lock:
            handle = self._require_open()
            if channel_format == "string":
                encoded_values = [
                    value if isinstance(value, bytes) else str(value).encode("utf-8")
                    for value in flat
                ]
                packed = b"".join(encoded_values)
                offsets = [0]
                for value in encoded_values:
                    offsets.append(offsets[-1] + len(value))
                packed_buffer, packed_pointer = self._buffer(packed)
                offset_array = (ctypes.c_uint64 * len(offsets))(*offsets)
                status = self.core._library.sr_xdf_writer_write_string_samples(
                    handle,
                    int(stream_id),
                    timestamps_array,
                    len(timestamps),
                    packed_pointer,
                    len(packed),
                    offset_array,
                    len(offsets),
                    channel_count,
                )
                del packed_buffer
                self.core._check(status, "write_string_samples")
                return
            try:
                format_id, value_type = VALUE_FORMATS[channel_format]
            except KeyError as error:
                raise ValueError(f"unsupported XDF channel format: {channel_format}") from error
            if channel_format.startswith("float") or channel_format == "double64":
                converted = [float(value) for value in flat]
            else:
                converted = [int(value) for value in flat]
                minimum, maximum = INTEGER_LIMITS[channel_format]
                if any(value < minimum or value > maximum for value in converted):
                    raise ValueError(f"sample exceeds {channel_format} range")
            value_array = (value_type * len(converted))(*converted)
            status = self.core._library.sr_xdf_writer_write_numeric_samples(
                handle,
                int(stream_id),
                timestamps_array,
                len(timestamps),
                value_array,
                len(converted),
                channel_count,
                format_id,
            )
            self.core._check(status, "write_numeric_samples")

    def write_clock_offset(self, stream_id: int, local_time: float, offset: float) -> None:
        with self._lock:
            status = self.core._library.sr_xdf_writer_write_clock_offset(
                self._require_open(), int(stream_id), float(local_time), float(offset)
            )
            self.core._check(status, "write_clock_offset")

    def boundary(self) -> None:
        with self._lock:
            status = self.core._library.sr_xdf_writer_write_boundary(self._require_open())
            self.core._check(status, "write_boundary")

    def flush(self, *, durable: bool = True) -> None:
        with self._lock:
            status = self.core._library.sr_xdf_writer_flush(
                self._require_open(), int(bool(durable))
            )
            self.core._check(status, "writer_flush")

    def close(self, *, durable: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            status = self.core._library.sr_xdf_writer_close(
                self._require_open(), int(bool(durable))
            )
            self.core._check(status, "writer_close")
            self._closed = True

    def abort(self, *, durable: bool = True) -> None:
        """Retain a flushed partial XDF without pretending its streams have footers."""

        with self._lock:
            if self._closed:
                return
            status = self.core._library.sr_xdf_writer_abort(
                self._require_open(), int(bool(durable))
            )
            self._closed = True
            self.core._check(status, "writer_abort")

    def destroy(self) -> None:
        with self._lock:
            if self._handle.value:
                self.core._library.sr_xdf_writer_destroy(self._handle)
                self._handle = ctypes.c_void_p()
            self._closed = True

    def __enter__(self) -> "NativeXdfWriter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if exc_type is None:
                self.close(durable=True)
            else:
                self.abort(durable=True)
        finally:
            self.destroy()
