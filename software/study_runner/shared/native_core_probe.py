"""Probe the native XDF core library without constructing a writer.

Moved out of `recording_worker.core` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.5): `recording/worker_binary.py`
(the host-side fail-closed core locator) needs this to validate a build
before it ever starts the worker, and `recording` may not import
`recording_worker` (invariant #1) -- the worker owns the actual writer, not
probing.

Split cleanly from `NativeXdfCore`/`NativeXdfWriter` (which stay in
`recording_worker.core`, per invariant #5: only the worker may write XDF
bytes): probing never constructs a writer, so nothing here depends on
anything writer-specific. `recording_worker.core` imports `NativeXdfError`,
`CoreProbe` and `probe_core_library` back from here for its own internal use
(the writer probes its own library on construction) and re-exports them so
existing callers keep working unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import json
from pathlib import Path
from typing import Any, Mapping


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
