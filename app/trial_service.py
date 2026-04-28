from __future__ import annotations

from typing import Any

from .integrations import brainbit_adapter, lsl_adapter, mini_radar_adapter, osc_adapter


def _build_marker(event: str, options: dict[str, Any]) -> str:
    """Build an LSL marker string with optional client timing metadata.

    Format: "start|ct=12345.67|offset=2.30"
    The ct (client_trigger_ms) and offset (clock_offset_ms) values allow
    post-hoc alignment of the LSL marker with the exact iPad screen event.
    """
    marker = event
    ct = options.get("client_trigger_ms")
    offset = options.get("clock_offset_ms")
    if isinstance(ct, (int, float)) and not isinstance(ct, bool):
        marker += f"|ct={ct:.2f}"
    if isinstance(offset, (int, float)) and not isinstance(offset, bool):
        marker += f"|offset={offset:.2f}"
    return marker


def start_trial_session(options: dict[str, Any] | None = None) -> None:
    """Send start signals to all active hardware integrations."""
    options = options or {}

    if options.get("send_signal", True):
        lsl_adapter.send_marker(_build_marker("start", options))
        osc_adapter.send_start()

    brainbit_adapter.set_routing(
        forward_to_lsl=options.get("brainbit_to_lsl", False),
        forward_to_touchdesigner=options.get("brainbit_to_touchdesigner", False),
    )
    mini_radar_adapter.set_recording(options.get("mini_radar_recording_enabled", False))
    print("[SERVER] Trial started")


def stop_trial_session(options: dict[str, Any] | None = None) -> None:
    """Send stop signals to all active hardware integrations."""
    options = options or {}

    if options.get("send_signal", True):
        lsl_adapter.send_marker(_build_marker("stop", options))
        osc_adapter.send_stop()

    brainbit_adapter.set_routing(
        forward_to_lsl=False,
        forward_to_touchdesigner=False,
    )
    mini_radar_adapter.set_recording(False)
    print("[SERVER] Trial stopped")
