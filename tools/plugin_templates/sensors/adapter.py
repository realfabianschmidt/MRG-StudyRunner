"""Acquisition for the example sensor -- the sensor data contract in its smallest form.

Every sensor plugin follows the same three-part pattern
(software/study_runner/plugin_framework/sensor_streams.py):

1. Raw: each real device sample becomes exactly one LSL sample, pushed with
   an explicit timestamp through ``SensorStreams``. The streams, channels and
   units come from manifest.json only. The recording worker writes them to
   XDF.
2. Backup: nothing to do here. The worker samples the manifest's
   ``backup_projection`` channels at the core's fixed 1 Hz.
3. Live: nothing to do here either. Every push also feeds the dashboard's
   live view of the manifest's ``live_view`` series (2 Hz, last 60 s).

Take the arrival time (``_streams.now()``) the moment a sample arrives, and
record what the device sends about itself (here ``seq``) as channels. Never
stamp a sample from ``time.time()`` or another host clock: the clock core owns
how clocks relate. A driver that hands over batches through callbacks
(``timing.timestamp_source: host_callback_reconstructed``) rebuilds its
timeline with ``clock_core.producer.callback_batch_start`` on a
``SourceClock`` and maps it with ``SourceToLsl``.

Replace ``_read_device`` with your device's read. It must never invent a
value: a sample that did not arrive is simply not pushed.

The shared recording worker owns XDF shutdown. Keep outlets available until
the session end marker has been sent and the worker has drained its inlets.
``stop()`` stops device acquisition; it must not write XDF footers or claim
that buffered LSL samples were captured. A worker cutoff is recorded in the
XDF footer and requires operator review before downstream use.
"""
from __future__ import annotations

import atexit
import threading
from typing import Any

from study_runner.plugin_framework.adapter_utils import set_state
from study_runner.plugin_framework.sensor_streams import SensorStreams

STREAM = "measurements"
READ_TIMEOUT_SECONDS = 0.25

_streams = SensorStreams.for_plugin(__file__)
_lock = threading.Lock()
_state_lock = threading.Lock()
_config: dict[str, Any] = {}
_running = False
# Bumped on every start()/stop() so a reader of an older run exits.
_generation = 0
_stop_event = threading.Event()
_reader: threading.Thread | None = None
_registered_shutdown = False
_latest_state: dict[str, Any] = {"status": "not_configured", "last_message": "Not configured yet."}


def initialize(*, enabled: bool = False, lsl_stream_prefix: str = "ExampleSensor") -> None:
    """Configure the adapter and start it if enabled."""
    global _config, _registered_shutdown
    _config = {"enabled": bool(enabled)}
    _streams.configure(name_prefix=lsl_stream_prefix)
    if not enabled:
        stop()
        _set_state({"status": "disabled", "last_message": "Example sensor is disabled."})
        return
    _streams.open_all()
    if not _registered_shutdown:
        atexit.register(stop)
        _registered_shutdown = True
    start()


def start() -> dict[str, Any]:
    global _running, _generation, _reader
    if not _config.get("enabled"):
        return get_status()
    with _lock:
        if _running:
            return get_status()
        _running = True
        _generation += 1
        _stop_event.clear()
        _reader = threading.Thread(target=_read_loop, args=(_generation,), daemon=True)
        _reader.start()
    _set_state({"status": "connecting", "last_message": "Waiting for the device."})
    return get_status()


def stop() -> dict[str, Any]:
    global _running, _generation
    with _lock:
        _running = False
        _generation += 1
        _stop_event.set()
    _set_state({"status": "stopped", "last_message": "Example sensor stopped."})
    return get_status()


def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_latest_state)
    running = bool(_running)
    status.update({
        "enabled": bool(_config.get("enabled")),
        "running": running,
        "lsl_enabled": _streams.is_open(STREAM),
        # Facts only; the core adds `ready` and `next_step`.
        "connection": {
            "phase": "connected" if running and status.get("status") == "connected" else ("connecting" if running else "off"),
            "device": None,
            "signal": {"state": "unknown"},
            "setup": {"state": "not_needed"},
            "streaming": running and status.get("status") == "connected",
        },
    })
    return status


def _read_loop(generation: int) -> None:
    sequence = 0
    while _running and generation == _generation:
        value = _read_device()
        if value is None:
            continue
        arrival = _streams.now()
        sequence += 1
        row = _streams.row(STREAM, {"value": value, "seq": sequence})
        _streams.push(STREAM, row, arrival)
        _set_state({"status": "connected", "last_message": "Receiving data."})


def _read_device() -> float | None:
    """One value from the device, or None when nothing arrived in time.

    Template placeholder: there is no device, so nothing ever arrives.
    Replace this with a blocking read that returns after READ_TIMEOUT_SECONDS.
    """
    _stop_event.wait(READ_TIMEOUT_SECONDS)
    return None


def _set_state(values: dict[str, Any]) -> None:
    set_state(_latest_state, _state_lock, values)
