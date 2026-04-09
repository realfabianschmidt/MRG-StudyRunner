from __future__ import annotations

from typing import Any

from .integrations import brainbit_adapter, lsl_adapter, osc_adapter


def start_trial_session(options: dict[str, Any] | None = None) -> None:
    """Send start signals to all active hardware integrations."""
    options = options or {}

    if options.get("send_signal", True):
        lsl_adapter.send_marker("start")
        osc_adapter.send_start()

    brainbit_adapter.set_routing(
        forward_to_lsl=options.get("brainbit_to_lsl", False),
        forward_to_touchdesigner=options.get("brainbit_to_touchdesigner", False),
    )
    print("[SERVER] Trial started")


def stop_trial_session(options: dict[str, Any] | None = None) -> None:
    """Send stop signals to all active hardware integrations."""
    options = options or {}

    if options.get("send_signal", True):
        lsl_adapter.send_marker("stop")
        osc_adapter.send_stop()

    brainbit_adapter.set_routing(
        forward_to_lsl=False,
        forward_to_touchdesigner=False,
    )
    print("[SERVER] Trial stopped")
