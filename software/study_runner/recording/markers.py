"""The study's own event-marker LSL outlet.

Every recording carries this stream unconditionally: it is how the study
lifecycle and card events get a timestamp inside the XDF, not something a study
author turns on or off. It lives here, in `recording/`, rather than under
`plugins/`, because it fails the test that defines a plugin --
`tests/test_plugin_removability.py` -- there is no software left to run
without it.

`markers.manifest.json` sits beside this file and is loaded through
`plugin_catalog.validate_and_normalize_manifest`, the exact function real
plugin manifests go through. That keeps the declared stream, its capabilities,
and this module's own constants from a single source instead of two hand-kept
copies -- but the manifest is never discovered from a directory scan and never
imported through an entry point, because there is nothing here to remove.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from study_runner.plugin_framework.dependency_utils import ensure_requirements
from study_runner.plugin_framework.plugin_catalog import validate_and_normalize_manifest


def _load_manifest() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "markers.manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return validate_and_normalize_manifest(payload, directory_name="markers")


MANIFEST = _load_manifest()
SOURCE_KEY = str(MANIFEST["plugin_key"])
CONFIG_KEY = str(MANIFEST["config_key"])
LSL_SOURCE_IDS = {stream["key"]: stream["source_id"] for stream in MANIFEST["streams"]}
LSL_CHANNEL_UNITS = {stream["key"]: tuple(stream["channel_units"]) for stream in MANIFEST["streams"]}

_outlet: Any = None
_local_clock: Any = None
_wall_to_lsl_offset: float | None = None
_last_lsl_timestamp: float | None = None
_lock = threading.RLock()


def initialize(hardware_config: Mapping[str, Any] | None = None) -> None:
    """Create the LSL outlet. Called once at server startup."""
    global _outlet
    config = hardware_config.get(CONFIG_KEY) if isinstance(hardware_config, Mapping) else None
    config = config if isinstance(config, Mapping) else {}
    stream_name = str(config.get("stream_name") or "StudyRunner")
    stream_type = str(config.get("stream_type") or "Markers")
    auto_install = bool(config.get("auto_install", True))
    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=auto_install,
        label="LSL",
    ):
        return
    try:
        from pylsl import StreamInfo, StreamOutlet, local_clock

        info = StreamInfo(
            name=stream_name,
            type=stream_type,
            channel_count=1,
            nominal_srate=0,  # irregular rate - markers are event-driven
            channel_format="string",
            source_id=LSL_SOURCE_IDS["markers"],
        )
        channel = info.desc().append_child("channels").append_child("channel")
        channel.append_child_value("label", "event")
        channel.append_child_value("unit", LSL_CHANNEL_UNITS["markers"][0])
        outlet = StreamOutlet(info)
        # Capture one mapping between wall time and LSL's monotonic clock.  A
        # stable offset avoids reintroducing wall-clock jumps for every event.
        with _lock:
            global _local_clock, _wall_to_lsl_offset, _last_lsl_timestamp
            _outlet = outlet
            _local_clock = local_clock
            _wall_to_lsl_offset = float(local_clock()) - time.time()
            _last_lsl_timestamp = None
        print(f"[MARKERS] Outlet ready: {stream_name} ({stream_type})")
    except ImportError:
        print("[MARKERS] pylsl import failed after dependency check.")


def stop() -> None:
    """Release the module-level LSL outlet."""
    global _outlet, _local_clock, _wall_to_lsl_offset, _last_lsl_timestamp
    with _lock:
        _outlet = None
        _local_clock = None
        _wall_to_lsl_offset = None
        _last_lsl_timestamp = None


def send_marker(value: str, *, server_epoch_ms: Any = None) -> dict[str, Any]:
    """Push one marker, optionally at an explicit server-wallclock instant.

    The route records its ingress/source timestamp before journal I/O and slow
    plugin callbacks.  Mapping that instant through the offset captured during
    initialization keeps the XDF marker on LSL's monotonic clock.  Explicit
    timestamps are clamped to non-decreasing order for this outlet; if no safe
    mapping is available the original ``push_sample([value])`` behavior is kept.
    """

    global _last_lsl_timestamp
    with _lock:
        outlet = _outlet
        if outlet is None:
            return {
                "sent": False,
                "marker_lsl_timestamp": None,
                "marker_push_epoch_ms": None,
            }
        requested_lsl_timestamp = _mapped_lsl_timestamp(server_epoch_ms)
        if requested_lsl_timestamp is None and _local_clock is not None:
            try:
                candidate = float(_local_clock())
            except (TypeError, ValueError, RuntimeError):
                candidate = math.nan
            if math.isfinite(candidate):
                requested_lsl_timestamp = candidate

        # This is the timestamp actually handed to pylsl, after the outlet's
        # ordering constraint has been applied. It is intentionally distinct
        # from the source-derived request: an older retry may be clamped.
        used_lsl_timestamp = requested_lsl_timestamp
        if used_lsl_timestamp is None:
            outlet.push_sample([value])
        else:
            if _last_lsl_timestamp is not None:
                used_lsl_timestamp = max(used_lsl_timestamp, _last_lsl_timestamp)
            outlet.push_sample([value], timestamp=used_lsl_timestamp)
            _last_lsl_timestamp = used_lsl_timestamp
        # Capture wall time only after push_sample returned. This reports the
        # actual push completion, not the requested/source event time.
        pushed_at_epoch_ms = round(time.time() * 1000.0, 3)
    print(f"[MARKERS] Marker sent: {value}")
    return {
        "sent": True,
        "marker_lsl_timestamp": used_lsl_timestamp,
        "marker_push_epoch_ms": pushed_at_epoch_ms,
    }


def _mapped_lsl_timestamp(server_epoch_ms: Any) -> float | None:
    if _wall_to_lsl_offset is None:
        return None
    try:
        epoch_ms = float(server_epoch_ms)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(epoch_ms) or epoch_ms < 0:
        return None
    mapped = (epoch_ms / 1000.0) + _wall_to_lsl_offset
    return mapped if math.isfinite(mapped) else None


def status() -> dict[str, Any]:
    with _lock:
        active = _outlet is not None
        return {
            "status": "enabled" if active else "waiting",
            "runtime_enabled": active,
            "device_label": "Study markers",
        }
