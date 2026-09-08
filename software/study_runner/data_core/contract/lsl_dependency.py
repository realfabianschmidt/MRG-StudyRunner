"""Check that pylsl/liblsl is actually usable, and report its version.

Moved out of `recording_worker.lsl_recording` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.5): the host's fail-closed preflight
(`backend/services/recording/recording_runtime.py`) needs to probe this
*before* starting the worker, and the worker itself needs the same check when
it actually opens its LSL inlets. Neither side should import the other's
module just for this -- it's a pure dependency probe with no dependency of
its own beyond `pylsl` itself.
"""
from __future__ import annotations

from importlib import metadata as importlib_metadata
from typing import Any


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
