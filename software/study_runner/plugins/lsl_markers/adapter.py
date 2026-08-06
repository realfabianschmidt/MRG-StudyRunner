"""
LSL adapter - sends event markers via the Lab Streaming Layer protocol.

How it fits into the recording workflow:
  1. This adapter creates an LSL outlet that broadcasts string markers on the network.
  2. The detached Study Runner recording worker captures this hidden provider into its
     own raw XDF segment through the native XDFWriter core.
  3. Finalization merges it exactly once with the native sensor streams and the marked
     derived-backup stream; PyXDF validates but never writes the result.
  4. Stable event IDs and original source times link the marker windows to the JSON result.

Requires: pylsl  (auto-install optional)
The provider is mandatory recording infrastructure and cannot be disabled by a generic setting.
"""
from __future__ import annotations
from typing import Any

from study_runner.plugin_framework.dependency_utils import ensure_requirements

# Module-level outlet reference. None means LSL is not active.
_outlet: Any = None
LSL_SOURCE_IDS = {"markers": "study_runner.markers"}
LSL_CHANNEL_UNITS = {"markers": ("event",)}


def initialize(stream_name: str, stream_type: str, auto_install: bool = True) -> None:
    """Create the LSL outlet. Called once at server startup when LSL is enabled."""
    global _outlet
    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=auto_install,
        label="LSL",
    ):
        return
    try:
        from pylsl import StreamInfo, StreamOutlet
        info    = StreamInfo(
            name=stream_name,
            type=stream_type,
            channel_count=1,
            nominal_srate=0,        # irregular rate - markers are event-driven
            channel_format='string',
            source_id=LSL_SOURCE_IDS["markers"],
        )
        channel = info.desc().append_child("channels").append_child("channel")
        channel.append_child_value("label", "event")
        channel.append_child_value("unit", LSL_CHANNEL_UNITS["markers"][0])
        _outlet = StreamOutlet(info)
        print(f"[LSL] Outlet ready: {stream_name} ({stream_type})")
    except ImportError:
        print("[LSL] pylsl import failed after dependency check.")


def stop() -> None:
    """Release the module-level LSL outlet."""
    global _outlet
    _outlet = None


def send_marker(value: str) -> None:
    """Push a string marker to the LSL outlet. Does nothing if LSL is not active."""
    if _outlet is not None:
        _outlet.push_sample([value])
        print(f"[LSL] Marker sent: {value}")
