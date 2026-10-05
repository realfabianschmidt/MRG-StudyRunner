"""AM Hub acquisition: one LSL sample per real board frame (sensor data contract).

The adapter holds the connection to the hub's ``/api/v2/stream`` (SSE) and
records what arrives, through the shared ``SensorStreams``:

- a ``frame`` from the radar, bio or solenoid board -> exactly one sample in
  that board's numeric stream (``radar``, ``bio``, ``valves``), stamped with
  the time it arrived here. The values are the ones the ESP sent: no tick, no
  unit conversion, nothing carried forward; a value missing from the frame is
  NaN. ``seq``, the hub's own ``t``, the estimated latency and its raw parts,
  and the applied correction travel along as bookkeeping channels.
- every received ``data:`` event, without exception, including every board
  frame -> its received JSON text, verbatim, in ``hub_events``, like
  BrainBit's ``diagnostics``. "Verbatim" means the JSON text as received, not
  the hub's radio/UART bytes. This is in addition to, never instead of, the
  numeric projection above - nothing the hub sent is ever discarded.
- once per ping attempt to the hub, successful or not -> one sample in
  ``hub_clock``: the hub's clock, the round trip and the clock offset
  estimated from it when the attempt succeeded; ``reply_valid`` says whether
  it did, and a failed attempt invents neither a hub time nor a usable
  offset for that row.

Latency (board -> Study Runner) is an *estimate* from two parts, neither a
direct measurement of the board's own acquisition instant:
``(arrival - t on our clock) + link_rtt_ms / 2``. The hub stamps ``t`` when a
board's packet reaches it; the pings' clock offset maps ``t`` onto this
computer's LSL clock. The hub pings each board itself and reports the radio
round trip as ``link_rtt_ms``; halving it assumes the up and down legs take
roughly the same time, which radio rarely guarantees exactly. The raw
``radio_rtt_ms`` and ``clock_offset_ms`` actually used travel with every
frame, separately from the combined ``latency_ms``, so a later analysis can
tell which part was stale or missing. A hub board-status reply older than
``STATUS_STALE_SECONDS`` no longer counts as a usable radio RTT; the frame is
still recorded, with ``latency_ms = NaN`` and no timestamp correction. By
default the timestamp stays the arrival time and the latency is only
recorded. With the machine setting ``timestamp_correction`` the timestamp
becomes ``arrival - latency``, clamped to never move a stream's timestamps
backwards - which can make the applied ``correction_ms`` smaller than the
estimated ``latency_ms`` for that same frame; ``correction_ms`` keeps the
applied amount reversible (add it back to recover the arrival time).

What the dashboard shows is ``monitor.py``'s job and the core's live view;
this module never derives a recorded value from them.

The hub also drives actuators. A stimulus card that selected this plugin
sends it start and stop (``send_stimulus_command``); what a stimulus does is
decided on the hub. Sensing never follows a card: it runs continuously.
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
from study_runner.plugin_framework.sensor_streams import SensorStreams
from study_runner.shared.clock_offset import ClockExchange, RoundTripOffsetEstimator
from .monitor import AmHubMonitor, channel_name

_streams = SensorStreams.for_plugin(__file__)
STREAM_CONTRACTS = _streams.declared_contracts()
# The hub names its boards by role; each role has its own recorded stream.
BOARD_STREAMS = {"radar": "radar", "bio": "bio", "solenoid": "valves"}
EVENTS_STREAM = "hub_events"
CLOCK_STREAM = "hub_clock"
# Channels every board stream carries besides the board's own values.
BOOKKEEPING_CHANNELS = (
    "rssi", "seq", "hub_timestamp", "latency_ms", "radio_rtt_ms", "clock_offset_ms", "correction_ms",
)
# A hub board-status reply older than this no longer answers for the board's
# *current* radio RTT - the frame still gets recorded, just without a usable
# radio RTT (latency_ms = NaN, no timestamp correction).
STATUS_STALE_SECONDS = 2.0
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

# The ESP's "no value" for these is 0; card averages skip it (XDF keeps it).
ZERO_MEANS_NO_VALUE = ("heartBpm", "breathRate", "personDist")

# The connection: v2 sends a status event 10x per second, so a stream silent
# for READ_TIMEOUT_SECONDS is dead and is replaced at once. The socket read
# timeout IS the watchdog: closing a socket from another thread does not
# interrupt a blocked read on Windows.
READ_TIMEOUT_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 3.0
# Reconnect at once, then back off briefly; never longer than 2 s.
RECONNECT_BACKOFF_SECONDS = (0.0, 0.5, 1.0, 2.0)
PING_INTERVAL_SECONDS = 1.0
PING_TIMEOUT_SECONDS = 2.0
# The first pings come faster, so the hub clock is known about a second after
# the start instead of after four (the estimate needs four exchanges).
QUICK_PINGS = 4
QUICK_PING_INTERVAL_SECONDS = 0.25
# A correction is applied only to a plausible latency; anything else is
# recorded as measured but leaves the timestamp at the arrival time.
MAX_CORRECTION_MS = 1000.0
# Recent latencies per board stream, for the dashboard's medians.
LATENCY_WINDOW = 50
# Stimulus start/stop for the hub's actuators: a short connect timeout, so an
# unreachable hub delays a trial event by at most about a second.
STIMULUS_COMMAND_TIMEOUT_SECONDS = (0.5, 1.0)
STIMULUS_COMMAND_FIELDS = ("event_id", "stimulus_id", "study_id", "session_id", "question_index", "source_epoch_ms")


class HubApiUnsupported(RuntimeError):
    """The hub has no ``/api/v2/stream`` (an older AM Hub)."""


_lock = threading.Lock()
_state_lock = threading.Lock()
_config: dict[str, Any] = {}
_running = False
# Bumped on every start()/stop() so an older reader exits on restart.
_generation = 0
_stop_event = threading.Event()
_registered_shutdown = False
_reader_thread: threading.Thread | None = None
_ping_thread: threading.Thread | None = None
_active_response: Any = None
_api_unsupported = False
_hub_rtts: deque[float] = deque(maxlen=5)
# The hub's clock seen from here, from the pings; reset on every start.
_clock = RoundTripOffsetEstimator()
# Frozen in start(): a run never switches between corrected and arrival times.
_correction_active = False
_latencies: dict[str, deque[float]] = {stream: deque(maxlen=LATENCY_WINDOW) for stream in BOARD_STREAMS.values()}
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
# The outcome of the last stimulus start/stop sent to the hub, for the dashboard.
_last_stimulus_command: dict[str, Any] = {}


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
    timestamp_correction: bool = False,
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
        "timestamp_correction": bool(timestamp_correction),
    }
    _streams.configure(name_prefix=lsl_stream_prefix, auto_install=bool(lsl_auto_install))
    if not enabled and _running:
        stop()
    _set_state({
        "status": "configured" if enabled else "disabled",
        "enabled": bool(enabled),
        "base_url": _config["base_url"],
        "last_message": "AM Hub adapter configured.",
    })
    if enabled:
        _streams.open_all()
    if not _registered_shutdown:
        atexit.register(stop)
        _registered_shutdown = True
    if enabled:
        start()


def start() -> dict[str, Any]:
    """Open the hub stream; every event is recorded once as it arrives."""
    global _running, _generation, _reader_thread, _ping_thread, _api_unsupported, _correction_active

    if not _config:
        _set_state({"status": "not_configured", "last_message": "AM Hub adapter is not configured."})
        return get_status()
    if not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "AM Hub is disabled in hardware settings."})
        return get_status()
    if not _config.get("base_url"):
        _set_state({"status": "waiting", "last_message": "AM Hub base URL is not configured."})
        return get_status()
    if not _streams.open_all():
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
        _api_unsupported = False
        _correction_active = bool(_config.get("timestamp_correction"))
        _generation += 1
        generation = _generation
        _stop_event.clear()
        # A new run starts with nothing from an earlier connection or participant.
        _monitor.reset()
        _history.clear()
        _hub_rtts.clear()
        _clock.reset()
        for latencies in _latencies.values():
            latencies.clear()
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


def send_stimulus_command(action: str, options: dict[str, Any]) -> dict[str, Any]:
    """Tell the hub that a stimulus starts or stops, for which card.

    ``POST {base_url}/api/v2/stimulus/start|stop`` with the card's identity;
    the hub decides what its actuators do. Prepared ahead of the hub: an
    unknown endpoint, an unreachable hub or a refusal is recorded in the
    status and never raised, so a stimulus is never held up by the hub.
    """
    import requests

    if action not in {"start", "stop"}:
        raise ValueError("Stimulus command must be start or stop.")
    payload = {field: options.get(field) for field in STIMULUS_COMMAND_FIELDS}
    result: dict[str, Any] = {
        "action": action,
        "event_id": payload["event_id"],
        "stimulus_id": payload["stimulus_id"],
        "sent_at": timestamp(time.time()),
    }
    base_url = str(_config.get("base_url") or "")
    if not _config.get("enabled") or not base_url:
        result.update(ok=False, outcome="not_configured", message="AM Hub is not configured; the stimulus command was not sent.")
    else:
        try:
            response = requests.post(
                f"{base_url}/api/v2/stimulus/{action}",
                json=payload,
                timeout=STIMULUS_COMMAND_TIMEOUT_SECONDS,
            )
        except Exception as error:
            result.update(ok=False, outcome="unreachable", message=f"AM Hub did not answer the stimulus {action}: {error}")
        else:
            result["http_status"] = response.status_code
            if response.ok:
                result.update(ok=True, outcome="accepted", message=f"AM Hub accepted the stimulus {action}.")
            elif response.status_code in (404, 405):
                result.update(ok=False, outcome="unsupported", message="This AM Hub does not handle stimulus commands yet.")
            else:
                result.update(ok=False, outcome="refused", message=f"AM Hub refused the stimulus {action} (HTTP {response.status_code}).")
    with _state_lock:
        _last_stimulus_command.clear()
        _last_stimulus_command.update(result)
    return dict(result)


def _stimulus_command_status() -> dict[str, Any] | None:
    with _state_lock:
        return dict(_last_stimulus_command) or None


def _alive(generation: int) -> bool:
    return _running and generation == _generation


# ------------------------------------------------------------------- status

def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_latest_state)
    status.update({
        "enabled": bool(_config.get("enabled", False)),
        "lsl_enabled": _streams.is_open("radar"),
        "base_url": _config.get("base_url", ""),
        "last_stimulus_command": _stimulus_command_status(),
        "streams": [contract["key"] for contract in _streams.contracts()],
        "auto_reconnect": bool(_config.get("auto_reconnect", True)),
        "api_unsupported": _api_unsupported,
        "running": bool(_running),
    })
    if not _running:
        # Off: no live values, graphs or age counters -- only the reason.
        status.update({
            "latest": {}, "topics": {}, "boards": {}, "hub_boards": {}, "hub_host": {},
            "person": {"detected": False, "sources": []}, "data_quality": {}, "unknown_topics": [],
            "hub_rtt_ms": None, "timing": {}, "last_activity_at": None, "seconds_since_last_activity": None,
        })
        status["connection"] = presence_sensor_connection(
            str(status.get("status") or "off"), running=False,
            message=str(status.get("last_message") or ""), device_label=_device_label({}),
        )
        return status

    now = time.time()
    fresh_seconds = float(_config.get("data_timeout_seconds", 5.0))
    view = _monitor.snapshot(now, fresh_seconds=fresh_seconds, hub_rtt_ms=_hub_rtt_ms())
    timing = _timing_status()
    for role, board in view["hub_boards"].items():
        board["latency_ms"] = timing["latency_ms"].get(BOARD_STREAMS.get(role, ""))
    status.update(view)
    status["timing"] = timing
    status["unknown_topics"] = sorted(address for address in view["topics"] if not _is_recorded_address(address))
    if view["last_frame_at"] is not None:
        status["last_activity_epoch"] = view["last_frame_at"]
        status["last_activity_at"] = timestamp(view["last_frame_at"])
        status["seconds_since_last_activity"] = round(max(0.0, now - view["last_frame_at"]), 3)
    status["link"] = _link_status(now)
    status.update(_live_status(status["link"], view["person"], _monitor.boards_ready(now, fresh_seconds)))
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

    The arrival time is taken first, before anything is parsed. Freshness is
    judged on this computer's clock; the hub's ``t`` only enters the latency.
    """
    arrival = _streams.now()
    received = time.time()
    _link_heard(received)
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        event = None
    if not isinstance(event, dict):
        _streams.push(EVENTS_STREAM, [data], arrival)  # not ours to judge: kept verbatim
        return
    role = str(event.get("role") or "")
    stream = BOARD_STREAMS.get(role) if event.get("type") == "frame" else None
    values = event.get("values") if isinstance(event.get("values"), dict) else None
    # Every received event is kept verbatim, with no exception for a normal
    # frame: the numeric projection below is in addition to this, never
    # instead of it.
    _streams.push(EVENTS_STREAM, [data], arrival)
    if stream and values is not None:
        latency_ms, radio_rtt_ms, clock_offset_ms = _frame_timing(event, role, arrival)
        row, complete = _board_row(stream, event, values, latency_ms, radio_rtt_ms, clock_offset_ms)
        _streams.push(stream, row, _frame_timestamp(arrival, latency_ms), arrival=arrival)
        if math.isfinite(latency_ms):
            _latencies[stream].append(latency_ms)
        _history.append({"_epoch": received, "stream": stream, **dict(zip(STREAM_CONTRACTS[stream]["channels"], row))})
    _monitor.observe(event, received)


def _frame_timing(event: dict[str, Any], role: str, arrival: float) -> tuple[float, float, float]:
    """One frame's ``(latency_ms, radio_rtt_ms, clock_offset_ms)``; NaN where unknown.

    ``radio_rtt_ms`` and ``clock_offset_ms`` are the raw parts *actually
    used* for ``latency_ms`` - recorded alongside it so a stale or missing
    part is visible instead of silently folded into one number.
    ``arrival - t`` (both on this computer's LSL clock, through the pings'
    clock offset) is the time from the hub to here, including the hub's own
    handling; ``link_rtt_ms / 2`` is the radio part from the board to the
    hub, measured by the hub's own pings (older board firmware has none, and
    a board-status reply older than ``STATUS_STALE_SECONDS`` no longer
    counts either).
    """
    hub_time = event.get("t")
    link_rtt = _monitor.link_rtt_ms(role, time.time())
    radio_rtt_ms = link_rtt if link_rtt is not None else math.nan
    estimate = _clock.estimate(arrival)
    clock_offset_ms = estimate.offset_s * 1000.0 if estimate.valid and estimate.offset_s is not None else math.nan
    if not estimate.valid or link_rtt is None or isinstance(hub_time, bool) or not isinstance(hub_time, (int, float)):
        return math.nan, radio_rtt_ms, clock_offset_ms
    local_hub_time = estimate.to_local(float(hub_time))
    if local_hub_time is None:
        return math.nan, radio_rtt_ms, clock_offset_ms
    latency_ms = (arrival - local_hub_time) * 1000.0 + link_rtt / 2.0
    return latency_ms, radio_rtt_ms, clock_offset_ms


def _frame_timestamp(arrival: float, latency_ms: float) -> float:
    """The arrival time, or with the correction on, the estimated sending time."""
    if _correction_active and math.isfinite(latency_ms) and 0.0 <= latency_ms <= MAX_CORRECTION_MS:
        return arrival - latency_ms / 1000.0
    return arrival


def _board_row(
    stream: str,
    event: dict[str, Any],
    values: dict[str, Any],
    latency_ms: float = math.nan,
    radio_rtt_ms: float = math.nan,
    clock_offset_ms: float = math.nan,
) -> tuple[list[float], bool]:
    """The frame's values in the stream's channel order; NaN where the frame has none.

    ``complete`` is False when the frame carries a value this stream does not
    declare -- the whole frame is kept verbatim in ``hub_events`` regardless
    (every event is, not only incomplete ones). ``correction_ms`` is filled
    in by ``SensorStreams`` when it publishes.
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
    by_name["latency_ms"] = latency_ms
    by_name["radio_rtt_ms"] = radio_rtt_ms
    by_name["clock_offset_ms"] = clock_offset_ms
    return [_number(by_name.get(channel)) for channel in STREAM_CONTRACTS[stream]["channels"]], complete


def _is_recorded_address(address: str) -> bool:
    return address in RSSI_ADDRESSES or any(channel_name(address) in names for names in BOARD_CHANNELS.values())


# ---------------------------------------------------------- card summaries

def get_interval_summary(start_epoch: float, end_epoch: float) -> dict[str, Any]:
    """Averages over the frames recorded in one card interval.

    The ESP sends 0 for "no value" (its own timeout). A heart or breathing
    rate or a distance of exactly 0 is therefore left out of the average and
    counted in ``zero_frames`` instead; the recorded XDF keeps every 0.
    """
    samples = samples_in_interval(_history, start_epoch, end_epoch)
    summary: dict[str, Any] = {
        "available": bool(samples),
        "sample_count": len(samples),
        "frame_counts": {stream: sum(1 for s in samples if s["stream"] == stream) for stream in BOARD_STREAMS.values()},
        "zero_frames": {},
        **truncation_info(_history, start_epoch),
    }
    for output, (stream, channel) in SUMMARY_CHANNELS.items():
        numbers = [s[channel] for s in samples if s["stream"] == stream and _finite(s.get(channel))]
        if channel in ZERO_MEANS_NO_VALUE:
            summary["zero_frames"][channel] = sum(1 for number in numbers if number == 0)
            numbers = [number for number in numbers if number != 0]
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
            if _api_unsupported or not _config.get("auto_reconnect", True):
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
    """Ping the hub once per second: one ``hub_clock`` sample per attempt.

    ``/api/v2/ping`` answers with ``server_now``, the hub's wall clock. With
    the send and receive times on this computer's LSL clock, a reply with a
    usable ``server_now`` is one exchange for the clock-offset estimate
    (``shared/clock_offset.py``). Every attempt gets a row, successful or
    not: an HTTP error or a timeout has no round trip to report either
    (``http_ok`` false); a reply that came back but carries no usable
    ``server_now`` still reports its round trip, just no hub time or
    exchange - either way, ``reply_valid`` says whether this row's hub time
    and clock offset are real, so a failure is visible rather than a row
    silently missing.
    """
    import requests

    session = requests.Session()
    answered = 0
    while _alive(generation):
        sent = _streams.now()
        try:
            response = session.get(f"{_config['base_url']}/api/v2/ping", timeout=PING_TIMEOUT_SECONDS)
            received = _streams.now()
            http_ok = bool(response.ok)
            server_now = _ping_server_now(response) if http_ok else None
        except Exception:
            received, http_ok, server_now = _streams.now(), False, None
        if _alive(generation):
            _record_ping(sent, received, server_now, http_ok=http_ok)
        if http_ok:
            answered += 1
        else:
            _hub_rtts.clear()  # an old round trip must not stand in for a failing one
        _stop_event.wait(QUICK_PING_INTERVAL_SECONDS if answered < QUICK_PINGS else PING_INTERVAL_SECONDS)
    session.close()


def _ping_server_now(response: Any) -> float | None:
    try:
        value = response.json().get("server_now")
    except Exception:
        return None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _record_ping(sent: float, received: float, server_now: float | None, *, http_ok: bool = True) -> None:
    """One ping attempt, successful or not: the offset estimate, one hub_clock sample.

    ``http_ok`` false (HTTP error or timeout) means no round trip either -
    the attempt never got far enough to measure one. ``http_ok`` true with
    ``server_now`` absent is a reply that came back with no usable hub time:
    the round trip is still real and recorded, only the hub time and
    exchange are not. ``reply_valid`` (derived from ``server_now``) is the
    one channel that tells the two apart from the rest of the row.
    """
    reply_valid = server_now is not None
    rtt_ms = math.nan
    exchange_offset = math.nan
    if http_ok:
        rtt_ms = (received - sent) * 1000.0
        _hub_rtts.append(rtt_ms)
    if reply_valid:
        exchange = ClockExchange(sent=sent, received=received, remote=server_now)
        exchange_offset = exchange.offset
        estimate = _clock.add(exchange)
    else:
        estimate = _clock.estimate(received)
    row = _streams.row(CLOCK_STREAM, {
        "hub_clock_s": server_now,
        "rtt_ms": rtt_ms,
        "exchange_offset_s": exchange_offset,
        "clock_offset_s": estimate.offset_s,
        "offset_uncertainty_ms": estimate.uncertainty_ms,
        "offset_valid": 1.0 if estimate.valid else 0.0,
        "offset_steps": estimate.steps,
        "correction_enabled": 1.0 if _correction_active else 0.0,
        "reply_valid": 1.0 if reply_valid else 0.0,
    })
    _streams.push(CLOCK_STREAM, row, received)


def _hub_rtt_ms() -> float | None:
    values = sorted(_hub_rtts)
    return round(values[len(values) // 2], 2) if values else None


def _timing_status() -> dict[str, Any]:
    """What the dashboard shows about timing: offset, round trip, latencies, correction.

    Ages the estimate to *now*, not to its own last exchange: without a real
    ``now``, a hub that stopped answering pings would keep reporting
    ``clock_offset_valid`` forever, because an estimate aged to itself never
    gets older.
    """
    estimate = _clock.estimate(_streams.now())
    return {
        "hub_rtt_ms": _hub_rtt_ms(),
        "clock_offset_valid": estimate.valid,
        "offset_uncertainty_ms": estimate.uncertainty_ms,
        "offset_steps": estimate.steps,
        "latency_ms": {stream: _median(values) for stream, values in _latencies.items()},
        "correction_enabled": bool(_correction_active),
        "correction_configured": bool(_config.get("timestamp_correction")),
    }


def _median(values: deque[float]) -> float | None:
    ordered = sorted(values)
    return round(ordered[len(ordered) // 2], 1) if ordered else None


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
