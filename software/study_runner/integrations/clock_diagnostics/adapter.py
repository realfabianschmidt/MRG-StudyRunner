"""Irregular LSL diagnostics that tie browser, wall, and LSL clocks together."""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

from ..dependency_utils import ensure_requirements


LSL_SOURCE_IDS = {"clock_diagnostics": "study_runner.clock_diagnostics"}
LSL_CHANNEL_UNITS = {
    "clock_diagnostics": (
        "millisecond",
        "second",
        "millisecond",
        "millisecond",
        "count",
        "millisecond",
        "count",
    )
}
CHANNELS = (
    "server_wall_epoch_ms",
    "lsl_local_clock_s",
    "client_clock_offset_ms",
    "clock_sync_rtt_ms",
    "client_sequence",
    "source_epoch_ms",
    "event_sequence",
)

_lock = threading.RLock()
_outlet: Any = None
_local_clock: Callable[[], float] | None = None
_event_sequence = 0
_last_activity_at: float | None = None


def initialize() -> None:
    """Create the mandatory internal outlet without mutating dependencies."""

    global _outlet, _local_clock
    if _outlet is not None:
        return
    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=False,
        label="Clock diagnostics LSL",
    ):
        return
    try:
        from pylsl import StreamInfo, StreamOutlet, local_clock

        info = StreamInfo(
            name="StudyRunnerClockDiagnostics",
            type="ClockDiagnostics",
            channel_count=len(CHANNELS),
            nominal_srate=0,
            channel_format="double64",
            source_id=LSL_SOURCE_IDS["clock_diagnostics"],
        )
        channels = info.desc().append_child("channels")
        for label, unit in zip(CHANNELS, LSL_CHANNEL_UNITS["clock_diagnostics"]):
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
            channel.append_child_value("unit", unit)
        with _lock:
            _local_clock = local_clock
            _outlet = StreamOutlet(info)
    except (ImportError, RuntimeError) as error:
        print(f"[CLOCK-DIAGNOSTICS] LSL outlet initialization failed: {error}")


def emit(options: dict[str, Any] | None = None) -> None:
    """Publish one timing observation at a trial or marker boundary."""

    global _event_sequence, _last_activity_at
    payload = options if isinstance(options, dict) else {}
    with _lock:
        outlet = _outlet
        local_clock = _local_clock
        if outlet is None or local_clock is None:
            return
        _event_sequence += 1
        event_sequence = _event_sequence

    lsl_now = float(local_clock())
    server_wall_ms = _finite(
        payload.get("server_received_epoch_ms"),
        default=time.time() * 1000.0,
    )
    sample = [
        server_wall_ms,
        lsl_now,
        _finite(payload.get("clock_offset_ms", payload.get("client_clock_offset_ms"))),
        _finite(payload.get("clock_sync_rtt_ms")),
        _finite(payload.get("sequence_number")),
        _finite(payload.get("source_epoch_ms", payload.get("client_trigger_epoch_ms"))),
        float(event_sequence),
    ]
    try:
        outlet.push_sample(sample, lsl_now)
        _last_activity_at = server_wall_ms
    except Exception as error:
        print(f"[CLOCK-DIAGNOSTICS] Could not push sample: {error}")


def status() -> dict[str, Any]:
    with _lock:
        active = _outlet is not None
        sequence = _event_sequence
    return {
        "status": "enabled" if active else "waiting",
        "configured_enabled": True,
        "runtime_enabled": active,
        "event_sequence": sequence,
        "last_activity_epoch_ms": _last_activity_at,
        "device_label": "Clock diagnostics",
    }


def stop() -> None:
    global _outlet, _local_clock
    with _lock:
        _outlet = None
        _local_clock = None


def _finite(value: Any, *, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default

