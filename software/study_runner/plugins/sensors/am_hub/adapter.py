"""AM Hub acquisition: one LSL sample per real board frame, like BrainBit.

The adapter holds the connection to the hub's ``/api/v2/stream`` (SSE) and
records what arrives:

- a ``frame`` from the radar, bio or solenoid board -> exactly one sample in
  that board's numeric stream (``radar``, ``bio``, ``valves``), stamped when it
  arrives here. The values are the ones the ESP sent: no tick, no unit
  conversion, nothing carried forward; a value missing from the frame is NaN.
  ``seq`` and the hub's own ``t`` travel along as bookkeeping channels.
- every other hub event (status, hello, valves, scene, gap, ...) -> verbatim
  JSON in ``hub_events``, like BrainBit's ``diagnostics``. A frame with a value
  its board stream does not declare goes there as well, so nothing is lost.

What the dashboard shows is ``monitor.py``'s job; this module never derives a
recorded value from it.
"""
from __future__ import annotations

import atexit
import codecs
import contextlib
import json
import math
import threading
import time
from collections import deque
from typing import Any

from study_runner.plugin_framework.adapter_utils import set_state, timestamp
from study_runner.plugin_framework.history_buffer import history_maxlen, max_gap_seconds, samples_in_interval, truncation_info
from study_runner.plugin_framework.sensor_connection import presence_sensor_connection
from study_runner.shared.dependency_utils import ensure_requirements
from study_runner.contracts.stream_contract import apply_stream_contract_desc, load_own_stream_contracts
from .monitor import AmHubMonitor, channel_name

STREAM_CONTRACTS = load_own_stream_contracts(__file__)
# The hub names its boards by role; each role has its own recorded stream.
BOARD_STREAMS = {"radar": "radar", "bio": "bio", "solenoid": "valves"}
EVENTS_STREAM = "hub_events"
# Channels every board stream carries besides the board's own values.
BOOKKEEPING_CHANNELS = ("rssi", "seq", "hub_timestamp")
# Over WiFi each board appends its signal strength under its own address.
RSSI_ADDRESSES = {"/sensor/rssiRadar", "/sensor/rssiBio", "/solenoid/rssi"}
BOARD_CHANNELS = {
    stream: tuple(c for c in STREAM_CONTRACTS[stream]["channels"] if c not in BOOKKEEPING_CHANNELS)
    for stream in BOARD_STREAMS.values()
}
# Interval summaries (per card) average these, as recorded.
SUMMARY_CHANNELS = {
    "avg_presence": ("radar", "presence"),
    "avg_move_energy": ("radar", "presMoveEnergy"),
    "avg_person_distance": ("radar", "personDist"),
    "avg_heart_rate": ("bio", "heartBpm"),
    "avg_breath_rate": ("bio", "breathRate"),
}

# The connection: v2 sends a status event 10x per second, so a stream silent
# for READ_TIMEOUT_SECONDS is dead and is replaced at once. The socket read
# timeout IS the watchdog: closing a socket from another thread does not
# interrupt a blocked read on Windows.
READ_TIMEOUT_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 3.0
# Reconnect at once, then back off briefly; never longer than 2 s.
RECONNECT_BACKOFF_SECONDS = (0.0, 0.5, 1.0, 2.0)
PING_INTERVAL_SECONDS = 1.0


class HubApiUnsupported(RuntimeError):
    """The hub has no ``/api/v2/stream`` (an older AM Hub)."""


_lock = threading.Lock()
_state_lock = threading.Lock()
_config: dict[str, Any] = {}
_running = False
# Bumped on every start()/stop() so an older reader exits on restart.
_generation = 0
_stop_event = threading.Event()
_recording_enabled = False
_registered_shutdown = False
_reader_thread: threading.Thread | None = None
_ping_thread: threading.Thread | None = None
_active_response: Any = None
_lsl_outlets: dict[str, Any] = {}
_publication_error: str | None = None
_api_unsupported = False
_hub_rtts: deque[float] = deque(maxlen=5)
# Recorded board samples for per-card summaries; sized for a full session.
_history: deque[dict[str, Any]] = deque(maxlen=history_maxlen(20.0))
_monitor = AmHubMonitor()
_latest_state: dict[str, Any] = {
    "status": "not_configured",
    "last_message": "AM Hub adapter has not been configured.",
}
# The connection to the hub, separate from what is recorded.
#   state            idle | connecting | open | lost
#   opened_at        when the current stream was opened (local epoch)
#   last_event_at    when the last byte-level sign of life arrived (local epoch)
_link_lock = threading.Lock()
_link: dict[str, Any] = {}
_event_times: deque[float] = deque(maxlen=400)


def _reset_link() -> None:
    with _link_lock:
        _link.clear()
        _link.update({
            "state": "idle", "opened_at": None, "last_event_at": None,
            "reconnects": 0, "last_loss": None, "last_error": "",
        })
        _event_times.clear()


_reset_link()


# ---------------------------------------------------------------- lifecycle

def initialize(
    *,
    enabled: bool = False,
    base_url: str = "",
    auto_reconnect: bool = True,
    data_timeout_seconds: float = 5.0,
    lsl_auto_install: bool = True,
    lsl_stream_prefix: str = "AmHub",
) -> None:
    """Configure the AM Hub adapter and start it if enabled."""
    global _config, _registered_shutdown

    _config = {
        "enabled": bool(enabled),
        "base_url": str(base_url or "").strip().rstrip("/"),
        "auto_reconnect": bool(auto_reconnect),
        "data_timeout_seconds": max(1.0, float(data_timeout_seconds)),
        "lsl_auto_install": bool(lsl_auto_install),
        "lsl_stream_prefix": lsl_stream_prefix,
    }
    if not enabled and _running:
        stop()
    _set_state({
        "status": "configured" if enabled else "disabled",
        "enabled": bool(enabled),
        "base_url": _config["base_url"],
        "last_message": "AM Hub adapter configured.",
    })
    if enabled:
        _initialize_lsl_outlets()
    if not _registered_shutdown:
        atexit.register(stop)
        _registered_shutdown = True
    if enabled:
        start()


def start() -> dict[str, Any]:
    """Open the hub stream; every event is recorded once as it arrives."""
    global _running, _generation, _reader_thread, _ping_thread, _publication_error, _api_unsupported

    if not _config:
        _set_state({"status": "not_configured", "last_message": "AM Hub adapter is not configured."})
        return get_status()
    if not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "AM Hub is disabled in hardware settings."})
        return get_status()
    if not _config.get("base_url"):
        _set_state({"status": "waiting", "last_message": "AM Hub base URL is not configured."})
        return get_status()
    if not _lsl_outlets:
        _initialize_lsl_outlets()
    if not _lsl_outlets:
        _set_state({"status": "failed", "last_message": "AM Hub LSL outlets are unavailable."})
        return get_status()

    with _lock:
        if _running:
            return get_status()
        old_threads = [t for t in (_reader_thread, _ping_thread) if t is not None]
    # Let the previous run's threads finish first (they exit on the
    # generation change; stop() cuts the stream, so the reader returns at once).
    for thread in old_threads:
        if thread is not threading.current_thread():
            thread.join(timeout=READ_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS)

    with _lock:
        if _running:
            return get_status()
        _running = True
        _publication_error = None
        _api_unsupported = False
        _generation += 1
        generation = _generation
        _stop_event.clear()
        # A new run starts with nothing from an earlier connection or participant.
        _monitor.reset()
        _history.clear()
        _hub_rtts.clear()
        _reset_link()
        _reader_thread = threading.Thread(target=_sse_loop, args=(generation,), daemon=True)
        _ping_thread = threading.Thread(target=_ping_loop, args=(generation,), daemon=True)
        _reader_thread.start()
        _ping_thread.start()

    _set_state({"status": "connecting", "last_message": f"Connecting to the AM Hub at {_config.get('base_url')}."})
    return get_status()


def stop() -> dict[str, Any]:
    """Close the stream and forget the live view; recorded XDF is untouched."""
    global _running, _generation

    with _lock:
        _running = False
        _generation += 1
        _stop_event.set()
        response = _active_response
    # Outside the lock: closing can wait for the reader's read to return.
    if response is not None:
        _abort_response(response)
    with _link_lock:
        _link["state"] = "idle"
    _monitor.reset()
    _set_state({"status": "stopped", "last_message": "AM Hub stream stopped."})
    return get_status()


def restart() -> dict[str, Any]:
    stop()
    return start()


def is_configured() -> bool:
    """Return True after initialize() stored AM Hub settings."""
    return bool(_config)


def set_auto_reconnect(enabled: bool) -> None:
    """The operator's auto-reconnect switch; the read loop checks it after each loss."""
    if _config:
        _config["auto_reconnect"] = bool(enabled)


def set_recording(enabled: bool) -> None:
    """Track the active stimulus phase without gating continuous LSL output."""
    global _recording_enabled
    _recording_enabled = bool(enabled)
    _set_state({
        "recording_enabled": _recording_enabled,
        "last_message": f"AM Hub recording {'enabled' if _recording_enabled else 'disabled'}.",
    })


def _alive(generation: int) -> bool:
    return _running and generation == _generation


# ------------------------------------------------------------------- status

def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_latest_state)
    status.update({
        "enabled": bool(_config.get("enabled", False)),
        "lsl_enabled": bool(_lsl_outlets),
        "recording_enabled": bool(_recording_enabled),
        "base_url": _config.get("base_url", ""),
        "streams": list(_lsl_outlets.keys()),
        "auto_reconnect": bool(_config.get("auto_reconnect", True)),
        "api_unsupported": _api_unsupported,
        "running": bool(_running),
    })
    if not _running:
        # Off: no live values, graphs or age counters -- only the reason.
        status.update({
            "latest": {}, "preview": {}, "topics": {}, "boards": {}, "hub_boards": {}, "hub_host": {},
            "person": {"detected": False, "sources": []}, "data_quality": {}, "unknown_topics": [],
            "hub_rtt_ms": None, "last_activity_at": None, "seconds_since_last_activity": None,
        })
        status["connection"] = presence_sensor_connection(
            str(status.get("status") or "off"), running=False,
            message=str(status.get("last_message") or ""), device_label=_device_label({}),
        )
        return status

    now = time.time()
    fresh_seconds = float(_config.get("data_timeout_seconds", 5.0))
    view = _monitor.snapshot(now, fresh_seconds=fresh_seconds, hub_rtt_ms=_hub_rtt_ms())
    status.update(view)
    status["unknown_topics"] = sorted(address for address in view["topics"] if not _is_recorded_address(address))
    if view["last_frame_at"] is not None:
        status["last_activity_epoch"] = view["last_frame_at"]
        status["last_activity_at"] = timestamp(view["last_frame_at"])
        status["seconds_since_last_activity"] = round(max(0.0, now - view["last_frame_at"]), 3)
    status["link"] = _link_status(now)
    status.update(_live_status(status["link"], view["person"], _monitor.boards_ready(now, fresh_seconds)))
    if _publication_error:
        status.update({"status": "failed", "last_message": _publication_error})
    status["connection"] = presence_sensor_connection(
        str(status.get("status") or ""),
        running=True,
        message=str(status.get("last_message") or ""),
        # The hub connects by itself when switched on, so the switch applies
        # at once; there is no manual connection to wait for.
        auto_reconnect=bool(_config.get("auto_reconnect", True)),
        had_connection=True,
        device_label=_device_label(view["hub_boards"]),
    )
    return status


def _live_status(link: dict[str, Any], person: dict[str, Any], missing_boards: list[str]) -> dict[str, Any]:
    """Ready only while the stream is open and radar and bio both send frames."""
    age = link.get("last_event_age_s")
    if link.get("state") == "open" and age is not None and age <= READ_TIMEOUT_SECONDS:
        if missing_boards:
            return {"status": "waiting",
                    "last_message": "AM Hub connected; waiting for fresh frames from " + ", ".join(missing_boards) + "."}
        if person["detected"]:
            return {"status": "connected", "last_message": "AM Hub connected; person detected."}
        return {"status": "no_presence", "last_message": "AM Hub connected; no person detected."}
    if _api_unsupported:
        return {"status": "failed", "last_message": "AM Hub API v2 is required; this hub only offers an older stream."}
    if age is None:
        error = link.get("last_error")
        message = f"AM Hub not reachable: {error}" if error else f"Connecting to the AM Hub at {_config.get('base_url')}."
        return {"status": "connecting", "last_message": message}
    reason = link.get("last_error") or (link.get("last_loss") or {}).get("reason") or "no data"
    return {"status": "stale", "last_message": f"Connection to the AM Hub lost ({reason}); reconnecting."}


def _device_label(hub_boards: dict[str, Any]) -> str:
    """The hub's address and how each board is attached (BLE / WiFi)."""
    url = str(_config.get("base_url") or "").split("://", 1)[-1]
    boards = [
        f"{role} {str(info.get('transport') or '').upper() or '?'}"
        for role, info in sorted(hub_boards.items()) if info.get("connected")
    ]
    return " · ".join([f"AM Hub {url}".strip(), *boards])


# -------------------------------------------------------------- recording

def _handle_sse_event(data: str) -> None:
    """Record one hub event, then let the monitor see it.

    Freshness is judged on this computer's clock: a value counts as received
    when it arrives here. The hub's ``t`` is recorded, never used for timing.
    """
    received = time.time()
    _link_heard(received)
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        event = None
    if not isinstance(event, dict):
        _push(EVENTS_STREAM, [data])  # not ours to judge: kept verbatim
        return
    stream = BOARD_STREAMS.get(str(event.get("role") or "")) if event.get("type") == "frame" else None
    values = event.get("values") if isinstance(event.get("values"), dict) else None
    if stream and values is not None:
        row, complete = _board_row(stream, event, values)
        _push(stream, row)
        _history.append({"_epoch": received, "stream": stream, **dict(zip(STREAM_CONTRACTS[stream]["channels"], row))})
        if not complete:
            _push(EVENTS_STREAM, [data])
    else:
        _push(EVENTS_STREAM, [data])
    _monitor.observe(event, received)


def _board_row(stream: str, event: dict[str, Any], values: dict[str, Any]) -> tuple[list[float], bool]:
    """The frame's values in the stream's channel order; NaN where the frame has none.

    ``complete`` is False when the frame carries a value this stream does not
    declare -- the caller then also keeps the whole frame in ``hub_events``.
    """
    by_name: dict[str, Any] = {}
    complete = True
    for address, value in values.items():
        if address in RSSI_ADDRESSES:
            by_name["rssi"] = value
        elif channel_name(address) in BOARD_CHANNELS[stream]:
            by_name[channel_name(address)] = value
        else:
            complete = False
    by_name["seq"] = event.get("seq")
    by_name["hub_timestamp"] = event.get("t")
    return [_number(by_name.get(channel)) for channel in STREAM_CONTRACTS[stream]["channels"]], complete


def _is_recorded_address(address: str) -> bool:
    return address in RSSI_ADDRESSES or any(channel_name(address) in names for names in BOARD_CHANNELS.values())


def _push(stream: str, row: list[Any]) -> None:
    """One sample to one outlet. A failed push is an acquisition error, not a sample."""
    global _publication_error
    outlet = _lsl_outlets.get(stream)
    if outlet is None:
        return
    try:
        outlet.push_sample(row)
    except Exception as error:
        _publication_error = f"AM Hub {stream} publication failed: {error}"
        _set_state({"status": "failed", "last_message": _publication_error})
        # Stops the reader: the run is failed until the next start().
        raise RuntimeError(_publication_error) from error


def _initialize_lsl_outlets() -> None:
    """One outlet per manifest stream, with its channels, units and contract."""
    global _lsl_outlets

    if not ensure_requirements(
        [("pylsl", "pylsl")],
        auto_install=bool(_config.get("lsl_auto_install", True)),
        label="AM Hub LSL",
    ):
        _lsl_outlets = {}
        return
    from pylsl import StreamInfo, StreamOutlet

    prefix = _config.get("lsl_stream_prefix", "AmHub")
    outlets = {}
    for key, stream in STREAM_CONTRACTS.items():
        info = StreamInfo(
            name=f"{prefix}_{stream['type']}",
            type=stream["type"],
            channel_count=len(stream["channels"]),
            nominal_srate=float(stream["nominal_rate_hz"]),
            channel_format=stream["channel_format"],
            source_id=stream["source_id"],
        )
        channels = info.desc().append_child("channels")
        for label, unit in zip(stream["channels"], stream["channel_units"], strict=True):
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
            channel.append_child_value("unit", unit)
        apply_stream_contract_desc(info, stream)
        outlets[key] = StreamOutlet(info)
    _lsl_outlets = outlets
    print("[AmHub] LSL outlets ready.")


# ---------------------------------------------------------- card summaries

def get_interval_summary(start_epoch: float, end_epoch: float) -> dict[str, Any]:
    """Averages over the frames recorded in one card interval, as recorded
    (the ESP's 0 for "no value" included -- see README)."""
    samples = samples_in_interval(_history, start_epoch, end_epoch)
    summary: dict[str, Any] = {
        "available": bool(samples),
        "sample_count": len(samples),
        "frame_counts": {stream: sum(1 for s in samples if s["stream"] == stream) for stream in BOARD_STREAMS.values()},
        **truncation_info(_history, start_epoch),
    }
    for output, (stream, channel) in SUMMARY_CHANNELS.items():
        numbers = [s[channel] for s in samples if s["stream"] == stream and _finite(s.get(channel))]
        summary[output] = round(sum(numbers) / len(numbers), 4) if numbers else None
    if samples:
        summary["max_gap_seconds"] = max_gap_seconds(samples)
    return summary


def export_interval_samples(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    """Recorded board samples of one interval, for the crash-recovery layout."""
    return [
        {("server_received_epoch" if key == "_epoch" else key): value for key, value in sample.items()}
        for sample in samples_in_interval(_history, start_epoch, end_epoch)
    ]


# --------------------------------------------------------------- connection

def _sse_loop(generation: int) -> None:
    """Hold one stream to the hub; replace a dead one at once.

    A stream counts as dead when the hub sends no byte for READ_TIMEOUT_SECONDS:
    the socket read times out and the reader returns. The next attempt
    follows immediately, then after 0.5 / 1 / 2 s.
    """
    global _running, _active_response, _api_unsupported

    session = _stream_session()
    failures = 0
    try:
        while _alive(generation):
            delay = RECONNECT_BACKOFF_SECONDS[min(failures, len(RECONNECT_BACKOFF_SECONDS) - 1)]
            if delay and _stop_event.wait(delay):
                break
            if not _alive(generation):
                break

            response = None
            reason = ""
            delivered = False
            _link_update(state="connecting")
            try:
                response = session.get(
                    f"{_config['base_url']}/api/v2/stream",
                    params={"client": _client_id()},
                    stream=True,
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                )
                if response.status_code == 404:
                    raise HubApiUnsupported("AM Hub API v2 is required; this hub only offers an older stream")
                response.raise_for_status()
                with _lock:
                    if not _alive(generation):
                        break
                    _active_response = response
                _link_opened()
                delivered = _read_sse_events(response, generation)
                reason = "the hub closed the stream"
            except HubApiUnsupported as error:
                _api_unsupported = True
                reason = str(error)
            except Exception as error:
                reason = _describe_error(error)
            finally:
                with _lock:
                    if _active_response is response:
                        _active_response = None
                if response is not None:
                    with contextlib.suppress(Exception):
                        response.close()

            if not _alive(generation):
                break
            _link_lost(reason)
            failures = 0 if delivered else failures + 1
            if _publication_error or _api_unsupported or not _config.get("auto_reconnect", True):
                break
    finally:
        with contextlib.suppress(Exception):
            session.close()
        with _lock:
            if generation == _generation:
                _running = False


def _read_sse_events(response: Any, generation: int | None = None) -> bool:
    """Hand every event over the moment its bytes arrive. True if any came.

    ``iter_lines`` waited for 512-byte blocks; ``iter_content(None)`` yields
    each chunk of the chunked stream as soon as it is received.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    data_lines: list[str] = []
    delivered = False
    chunked = getattr(getattr(response, "raw", None), "chunked", True)
    for chunk in response.iter_content(chunk_size=None if chunked is not False else 1):
        if not _running or (generation is not None and generation != _generation):
            break
        if not chunk:
            continue
        pending += decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            line = line.rstrip("\r")
            if line == "":
                if data_lines:
                    _handle_sse_event("\n".join(data_lines))
                    delivered = True
                    data_lines = []
                continue
            if line.startswith(":"):
                _link_heard()  # keepalive: the hub is there
                continue
            if line.startswith("data:"):
                value = line[len("data:"):]
                data_lines.append(value[1:] if value.startswith(" ") else value)
            # "event:"/"id:"/"retry:" are unused: the hub sends default
            # "message" events, told apart by their "type" field.
    return delivered


def _stream_session() -> Any:
    """A requests session whose sockets notice a dead peer and send at once."""
    import socket

    import requests
    from requests.adapters import HTTPAdapter

    options = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1), (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for name, value in (("TCP_KEEPIDLE", 5), ("TCP_KEEPINTVL", 2), ("TCP_KEEPCNT", 3)):
        if hasattr(socket, name):
            options.append((socket.IPPROTO_TCP, getattr(socket, name), value))
    usable = []
    probe = socket.socket()
    try:
        for option in options:
            try:
                probe.setsockopt(*option)
                usable.append(option)
            except OSError:
                pass  # not supported on this platform: skip it, never fail the connect
    finally:
        probe.close()

    class _KeepAliveAdapter(HTTPAdapter):
        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            kwargs["socket_options"] = usable
            super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("http://", _KeepAliveAdapter())
    session.mount("https://", _KeepAliveAdapter())
    return session


def _client_id() -> str:
    """One stable name per computer: the hub replaces this computer's old stream."""
    import socket

    return f"study-runner-{socket.gethostname()}"


def _describe_error(error: Exception) -> str:
    text = str(error) or type(error).__name__
    if "read timed out" in text.lower():
        return f"no data from the hub for {READ_TIMEOUT_SECONDS:.0f} s"
    return text if len(text) <= 160 else text[:157] + "..."


def _abort_response(response: Any) -> None:
    """Cut a stream so the thread blocked reading it returns at once.

    ``close()`` alone does not interrupt a read blocked in another thread on
    every platform (on Windows the reader sat out its read timeout); shutting
    the socket down does.
    """
    import socket

    sock = None
    raw = getattr(response, "raw", None)
    for path in (("_connection", "sock"), ("_fp", "fp", "raw", "_sock")):
        candidate: Any = raw
        for name in path:
            candidate = getattr(candidate, name, None)
            if candidate is None:
                break
        if candidate is not None and hasattr(candidate, "shutdown"):
            sock = candidate
            break
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(Exception):
        response.close()


def _link_update(**values: Any) -> None:
    with _link_lock:
        _link.update(values)


def _link_opened() -> None:
    with _link_lock:
        _link.update(state="open", opened_at=time.time(), last_error="")


def _link_heard(at: float | None = None) -> None:
    moment = at if at is not None else time.time()
    with _link_lock:
        _link["last_event_at"] = moment
    _event_times.append(moment)


def _link_lost(reason: str) -> None:
    now = time.time()
    with _link_lock:
        was_open = _link.get("state") == "open"
        _link.update(state="lost", last_error=reason)
        if was_open:
            _link["reconnects"] = int(_link.get("reconnects") or 0) + 1
            _link["last_loss"] = {"at": timestamp(now), "epoch": now, "reason": reason}
    if was_open:
        print(f"[AmHub] Connection lost ({reason}); reconnecting.")


def _link_status(now: float) -> dict[str, Any]:
    with _link_lock:
        link = dict(_link)
    recent = [moment for moment in list(_event_times) if now - moment <= 2.0]
    last_event = link.get("last_event_at")
    return {
        "state": link.get("state"),
        "connected_for_s": round(now - link["opened_at"], 1) if link.get("state") == "open" and link.get("opened_at") else None,
        "last_event_age_s": round(max(0.0, now - last_event), 2) if last_event else None,
        "events_per_s": round(len(recent) / 2.0, 1),
        "reconnects": int(link.get("reconnects") or 0),
        "last_loss": link.get("last_loss"),
        "last_error": link.get("last_error") or "",
    }


def _ping_loop(generation: int) -> None:
    """This computer's HTTP round trip to the hub, once per second (diagnostic only)."""
    import requests

    session = requests.Session()
    while _alive(generation):
        started = time.perf_counter()
        try:
            ok = session.get(f"{_config['base_url']}/api/v2/ping", timeout=2.0).ok
        except Exception:
            ok = False
        if ok:
            _hub_rtts.append((time.perf_counter() - started) * 1000.0)
        else:
            _hub_rtts.clear()  # an old round trip must not stand in for a failing one
        _stop_event.wait(PING_INTERVAL_SECONDS)
    session.close()


def _hub_rtt_ms() -> float | None:
    values = sorted(_hub_rtts)
    return round(values[len(values) // 2], 2) if values else None


# ------------------------------------------------------------------ helpers

def _set_state(values: dict[str, Any]) -> None:
    set_state(_latest_state, _state_lock, values)


def _number(value: Any) -> float:
    """A channel value; anything missing or not a number is NaN, never 0."""
    if value is None or value == "":
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)
