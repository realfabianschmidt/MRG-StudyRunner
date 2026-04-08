"""
OSC adapter — sends Open Sound Control messages to TouchDesigner or any OSC host.

OSC (Open Sound Control) is a lightweight message format used by tools like TouchDesigner,
Max/MSP, and other audiovisual systems. The adapter sends a message when the stimulus
starts and another when it stops.

Configure the target host, port, and message addresses in hardware_config.json:
  {
    "osc": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 9000,
      "address_start": "/study/start",
      "address_stop": "/study/stop"
    }
  }

Requires: python-osc  (already in requirements.txt)
Enable:   set "osc": { "enabled": true } in hardware_config.json
"""
from __future__ import annotations
from typing import Any

# Module-level state. None means OSC is not active.
_client:        Any = None
_address_start: str = "/study/start"
_address_stop:  str = "/study/stop"


def initialize(
    host:          str = "127.0.0.1",
    port:          int = 9000,
    address_start: str = "/study/start",
    address_stop:  str = "/study/stop",
) -> None:
    """Create the OSC UDP client. Called once at server startup when OSC is enabled."""
    global _client, _address_start, _address_stop
    _address_start = address_start
    _address_stop  = address_stop
    try:
        from pythonosc.udp_client import SimpleUDPClient
        _client = SimpleUDPClient(host, port)
        print(f"[OSC] Client ready: {host}:{port}  (start={address_start}, stop={address_stop})")
    except ImportError:
        print("[OSC] python-osc not installed — install with: pip install python-osc")


def send_start() -> None:
    """Send the configured start message. Does nothing if OSC is not active."""
    _send(_address_start, 1)


def send_stop() -> None:
    """Send the configured stop message. Does nothing if OSC is not active."""
    _send(_address_stop, 0)


def _send(address: str, value: Any) -> None:
    """Internal helper — send a single OSC message."""
    if _client is not None:
        _client.send_message(address, value)
        print(f"[OSC] Sent {address} = {value}")
