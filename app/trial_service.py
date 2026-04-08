"""
Trial service — coordinates start and stop signals across all active hardware integrations.

Each integration is handled by a small adapter in app/integrations/. If the required
library is not installed the adapter does nothing, so the study always runs normally
regardless of which hardware is connected.

To add a new integration: create a new adapter in app/integrations/ and call it here.
"""
from .integrations import lsl_adapter, osc_adapter


def start_trial_session() -> None:
    """Send start signals to all active hardware integrations."""
    lsl_adapter.send_marker("start")
    osc_adapter.send_start()
    print("[SERVER] Trial started")


def stop_trial_session() -> None:
    """Send stop signals to all active hardware integrations."""
    lsl_adapter.send_marker("stop")
    osc_adapter.send_stop()
    print("[SERVER] Trial stopped")
