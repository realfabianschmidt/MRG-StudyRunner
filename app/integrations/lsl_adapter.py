"""
LSL adapter - sends event markers via the Lab Streaming Layer protocol.

How it fits into the recording workflow:
  1. This adapter creates an LSL outlet that broadcasts string markers on the network.
  2. LabRecorder (a separate, standalone tool) listens on the LSL network and records
     all active streams - EEG from BrainBit, markers from this adapter - into one .xdf file.
  3. After the study, use pyxdf to load the .xdf file, then MNE-Python to process the EEG.
  4. The participant_id and timestamp_start in the JSON result file link the answers
     to the EEG recording by time.

Requires: pylsl  (auto-install optional)
Enable:   set "lsl": { "enabled": true } in hardware_config.json
"""
from __future__ import annotations
from typing import Any

from .dependency_utils import ensure_requirements

# Module-level outlet reference. None means LSL is not active.
_outlet: Any = None


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
        )
        _outlet = StreamOutlet(info)
        print(f"[LSL] Outlet ready: {stream_name} ({stream_type})")
    except ImportError:
        print("[LSL] pylsl import failed after dependency check.")


def send_marker(value: str) -> None:
    """Push a string marker to the LSL outlet. Does nothing if LSL is not active."""
    if _outlet is not None:
        _outlet.push_sample([value])
        print(f"[LSL] Marker sent: {value}")
