"""AM Hub adapter for presence, position, movement, vitals, valve and link data.

The AM Hub is a Raspberry Pi service that controls the Parasite autonomous
material and exposes its sensor readings over a small, unauthenticated
HTTP/SSE API. This adapter is a client of that API: it opens a long-lived
Server-Sent-Events connection to ``{base_url}/api/v2/stream`` (falling back
to ``/api/v1/stream`` on older hubs), caches the latest value per OSC-style
topic address, and republishes a combined sample per declared stream on a
fixed 10 Hz tick -- this adapter's own clock decides timing and freshness;
hub timestamps are never used for that.

v2 delivers each board packet as one ``frame`` (all values of the packet at
once, so a tick never sees half an update), plus the hub's per-board status
(link, transport, RSSI, rate, lost packets, latency parts) and valve/scene
state. A separate 1 Hz ping thread measures this adapter's round trip to the
hub, completing the per-board latency (see README.md).
"""
from __future__ import annotations

import atexit
import codecs
import contextlib
import json
import threading
import time
from collections import deque
from typing import Any

from study_runner.plugin_framework.adapter_utils import set_state, timestamp
from study_runner.shared.dependency_utils import ensure_requirements
from study_runner.plugin_framework.history_buffer import history_maxlen, max_gap_seconds, samples_in_interval, truncation_info
from study_runner.contracts.stream_contract import apply_stream_contract_desc, load_own_stream_contracts
from study_runner.plugin_framework.sensor_connection import presence_sensor_connection


# Manifest-declared channels per stream, and the AM Hub OSC topic each one
# reads from. Kept as the single source of truth for normalization, LSL
# outlet creation and publishing -- add a channel here and to manifest.json
# together.
STREAM_CHANNELS: dict[str, tuple[str, ...]] = {
    "presence": ("presence", "presState", "presDist", "moveEnergy", "staticEnergy", "targetCount"),
    "position": (
        "personDist", "personX", "personY",
        "t1x", "t1y", "t1speed",
        "t2x", "t2y", "t2speed",
        "t3x", "t3y", "t3speed",
    ),
    "vitals": ("heartRate", "breathRate", "bioDistance", "bioX", "bioY"),
    "radar_detail": ("t1res", "t2res", "t3res", "presDetDist"),
    "valves": ("ch0", "ch1", "ch2", "ch3", "ch4", "ch5", "ch6", "ch7", "sceneActive"),
    "hub_status": (
        "radarConnected", "radarTransport", "radarRssi", "radarRateHz", "radarLost", "radarLinkRttMs", "radarLatencyMs",
        "bioConnected", "bioTransport", "bioRssi", "bioRateHz", "bioLost", "bioLinkRttMs", "bioLatencyMs",
        "solenoidConnected", "solenoidTransport", "solenoidRssi", "solenoidRateHz", "solenoidLost",
        "solenoidLinkRttMs", "solenoidLatencyMs",
        "hubRttMs", "hubDroppedEvents",
    ),
}
CHANNEL_TOPICS: dict[str, str] = {
    "presence": "/sensor/presence",
    "presState": "/sensor/presState",
    "presDist": "/sensor/presDist",
    "moveEnergy": "/sensor/presMoveEnergy",
    "staticEnergy": "/sensor/presStaticEnergy",
    "targetCount": "/sensor/targetCount",
    "personDist": "/sensor/personDist",
    "personX": "/sensor/personX",
    "personY": "/sensor/personY",
    "t1x": "/sensor/t1x", "t1y": "/sensor/t1y", "t1speed": "/sensor/t1speed",
    "t2x": "/sensor/t2x", "t2y": "/sensor/t2y", "t2speed": "/sensor/t2speed",
    "t3x": "/sensor/t3x", "t3y": "/sensor/t3y", "t3speed": "/sensor/t3speed",
    "heartRate": "/sensor/heartBpm",
    "breathRate": "/sensor/breathRate",
    "bioDistance": "/sensor/bioDist",
    "bioX": "/sensor/bioT1x",
    "bioY": "/sensor/bioT1y",
    "t1res": "/sensor/t1res", "t2res": "/sensor/t2res", "t3res": "/sensor/t3res",
    "presDetDist": "/sensor/presDetDist",
    "ch0": "/solenoid/CH0", "ch1": "/solenoid/CH1", "ch2": "/solenoid/CH2", "ch3": "/solenoid/CH3",
    "ch4": "/solenoid/CH4", "ch5": "/solenoid/CH5", "ch6": "/solenoid/CH6", "ch7": "/solenoid/CH7",
}
# Channels that are hub state rather than board topics (v2 status/scene
# events). They live in the same topic cache under "hub:" pseudo topics, so
# they share the staleness rule and the fixed 10 Hz tick.
HUB_ROLES = ("radar", "bio", "solenoid")
TRANSPORT_CODES = {"ble": 1.0, "wifi": 2.0}
for _role in HUB_ROLES:
    for _field in ("Connected", "Transport", "Rssi", "RateHz", "Lost", "LinkRttMs", "LatencyMs"):
        CHANNEL_TOPICS[f"{_role}{_field}"] = f"hub:{_role}{_field}"
CHANNEL_TOPICS.update({
    "sceneActive": "hub:sceneActive",
    "hubRttMs": "hub:hubRttMs",
    "hubDroppedEvents": "hub:hubDroppedEvents",
})
TOPIC_CHANNELS: dict[str, str] = {topic: channel for channel, topic in CHANNEL_TOPICS.items()}
# Hub values arrive in the firmware's units; the stream contract is mm and
# mm/s. MR60 bio distance is cm, LD2450 target speed is cm/s.
CHANNEL_SCALE: dict[str, float] = {
    "bioDistance": 10.0,
    "t1speed": 10.0, "t2speed": 10.0, "t3speed": 10.0,
}
# The firmware sends 0 instead of "no value" (sensor silent for 2 s, no
# target). A heart/breath rate or distance of exactly 0 is never a reading.
ZERO_MEANS_MISSING: tuple[str, ...] = ("heartRate", "breathRate", "bioDistance")
# A target at x = y = 0 is an empty slot (the firmware's own rule): its whole
# group is missing. The nearest target is missing when no target is tracked.
EMPTY_AT_ORIGIN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("bioX", "bioY", ("bioX", "bioY")),
    ("t1x", "t1y", ("t1x", "t1y", "t1speed", "t1res")),
    ("t2x", "t2y", ("t2x", "t2y", "t2speed", "t2res")),
    ("t3x", "t3y", ("t3x", "t3y", "t3speed", "t3res")),
)
NEAREST_TARGET_CHANNELS: tuple[str, ...] = ("personDist", "personX", "personY")
# Every hub event, verbatim, as one JSON string per event: frames with all
# their values, seq, sender and hub time, status, valves, scene, gap, hello,
# plus this adapter's own "link" events when the connection drops or returns.
# Nothing the hub sends is filtered, converted or lost in the recording --
# including topics this adapter does not know yet.
EVENTS_STREAM = "hub_events"
LSL_SOURCE_IDS = {key: f"study_runner.am_hub.{key}" for key in (*STREAM_CHANNELS, EVENTS_STREAM)}
LSL_CHANNEL_UNITS: dict[str, tuple[str, ...]] = {
    "presence": ("boolean", "enum", "millimetre", "arbitrary_unit", "arbitrary_unit", "count"),
    "position": (
        "millimetre", "millimetre", "millimetre",
        "millimetre", "millimetre", "millimetre_per_second",
        "millimetre", "millimetre", "millimetre_per_second",
        "millimetre", "millimetre", "millimetre_per_second",
    ),
    "vitals": ("beats_per_minute", "breaths_per_minute", "millimetre", "millimetre", "millimetre"),
    "radar_detail": ("millimetre",) * 4,
    "valves": ("boolean",) * 9,
    "hub_status": ("boolean", "enum", "decibel_milliwatt", "hertz", "count", "millisecond", "millisecond") * 3
    + ("millisecond", "count"),
    EVENTS_STREAM: ("json",),
}
# Dashboard trend-graph channels -- a subset of STREAM_CHANNELS, matching
# ui/dashboard.js's TREND_CONFIG.
TREND_MOVEMENT_CHANNELS: tuple[str, ...] = ("moveEnergy", "staticEnergy")
TREND_POSITION_CHANNELS: tuple[str, ...] = ("personX", "personY")
TREND_VITALS_CHANNELS: tuple[str, ...] = ("heartRate", "breathRate")
PUBLISH_RATE_HZ = 10.0
# The combined sample publishes on a fixed tick regardless of whether the hub
# sent anything new -- a topic older than this is republished as missing
# (None -> NaN) rather than silently carried forward as if it were live.
# Matches manifest.json's capabilities.backup_projection.stale_after_ms.
TOPIC_STALE_SECONDS = 2.5
# v2 sends a status event 10x per second, so a v2 connection silent for 2 s
# is dead (WiFi dropout, hub restart) and is replaced at once. v1 only sends
# a keepalive every 15 s. The socket read timeout IS the watchdog: it ends a
# silent stream on every platform, whereas closing a socket from another
# thread does not interrupt a blocked read on Windows.
LINK_IDLE_SECONDS = {"v2": 2.0, "v1": 35.0}
READ_TIMEOUT_SECONDS = dict(LINK_IDLE_SECONDS)
CONNECT_TIMEOUT_SECONDS = 3.0
# Reconnect at once, then back off briefly; never longer than 2 s.
RECONNECT_BACKOFF_SECONDS = (0.0, 0.5, 1.0, 2.0)
PING_INTERVAL_SECONDS = 1.0
# A person counts as detected when any fresh source sees one: the LD2410B
# presence flag, a tracked LD2450 target, or MR60 distance / vital signs.
PERSON_FLAG_TOPIC = "/sensor/presence"
PERSON_TARGETS_TOPIC = "/sensor/targetCount"
PERSON_VITAL_TOPICS = ("/sensor/bioDist", "/sensor/heartBpm", "/sensor/breathRate")
# Package 5d (docs/archive/architecture-1.0-umbau.md): this plugin's own frozen
# stream contract, for the desc/study_runner XDF header block only.
STREAM_CONTRACTS = load_own_stream_contracts(__file__)

_lock = threading.Lock()
_state_lock = threading.Lock()
_topics_lock = threading.Lock()
_preview_lock = threading.Lock()
_config: dict[str, Any] = {}
_running = False
# Bumped on every start()/stop(): threads of an older run see the change and
# exit, so a restart can never leave two publishers pushing the same stream.
_generation = 0
_stop_event = threading.Event()
_recording_enabled = False
_registered_shutdown = False
_reader_thread: threading.Thread | None = None
_publisher_thread: threading.Thread | None = None
_active_response: Any = None
_lsl_outlets: dict[str, Any] = {}
_topics: dict[str, dict[str, Any]] = {}
# When any topic last actually updated -- unlike a sample's own _epoch
# (always "now", since samples publish on a fixed tick), this is the true
# freshness signal get_status() uses to detect a stalled hub connection.
_last_topic_update_epoch: float | None = None
_api_version: str | None = None
_ping_thread: threading.Thread | None = None
_hub_rtts: deque[float] = deque(maxlen=5)
_hub_boards: dict[str, dict[str, Any]] = {}
_hub_dropped_events = 0
# What the hub says about its own host (hello event), e.g. WiFi power saving.
_hub_host: dict[str, Any] = {}
_frame_counts: dict[str, int] = {}
# Arrival times of the last frames per board (for its live rate and age).
_frame_times: dict[str, deque[float]] = {}
_seq_gaps: dict[str, int] = {}
_last_seq: dict[str, int] = {}
_lost_baseline: dict[str, float] = {}
# Dashboard trend graphs: a bounded, ~1 Hz preview per graph, same shape and
# cadence as the BrainBit dashboard's preview (at/received_at/values/validity)
# so its ui/dashboard.js auto-scale/gap logic can be ported unchanged.
_preview: dict[str, deque[dict[str, Any]]] = {
    "movement": deque(maxlen=60),
    "position": deque(maxlen=60),
    "vitals": deque(maxlen=60),
}
_last_preview_epoch = 0.0
# AM Hub reports at ~10 Hz; sized to hold a full study session.
_history: deque[dict[str, Any]] = deque(maxlen=history_maxlen(PUBLISH_RATE_HZ))
_latest_state: dict[str, Any] = {
    "status": "not_configured",
    "latest": {},
    "last_message": "AM Hub adapter has not been configured.",
}
# The connection to the hub, kept apart from the data: only the stream
# reader changes it, so the 10 Hz publisher can never claim "connected".
#   state            idle | connecting | open | lost
#   opened_at        when the current stream was opened (local epoch)
#   last_event_at    when the last event of any kind arrived (local epoch)
#   close_reason     set by the watchdog before it closes a silent stream
_link_lock = threading.Lock()
_link: dict[str, Any] = {}
_event_times: deque[float] = deque(maxlen=400)


def _reset_link() -> None:
    with _link_lock:
        _link.clear()
        _link.update({
            "state": "idle", "opened_at": None, "last_event_at": None, "close_reason": "",
            "reconnects": 0, "last_loss": None, "last_error": "",
        })
        _event_times.clear()


_reset_link()


def initialize(
    *,
    enabled: bool = False,
    base_url: str = "",
    auto_reconnect: bool = True,
    reconnect_delay_seconds: float = 3.0,
    data_timeout_seconds: float = 5.0,
    lsl_enabled: bool = False,
    lsl_auto_install: bool = True,
    lsl_stream_prefix: str = "AmHub",
) -> None:
    """Configure the AM Hub adapter and start it if enabled."""
    global _config, _registered_shutdown

    _config = {
        "enabled": bool(enabled),
        "base_url": str(base_url or "").strip().rstrip("/"),
        "auto_reconnect": bool(auto_reconnect),
        "reconnect_delay_seconds": max(1.0, float(reconnect_delay_seconds)),
        "data_timeout_seconds": max(1.0, float(data_timeout_seconds)),
        "lsl_enabled": bool(lsl_enabled),
        "lsl_auto_install": bool(lsl_auto_install),
        "lsl_stream_prefix": lsl_stream_prefix,
    }

    _set_state(
        {
            "status": "configured" if enabled else "disabled",
            "enabled": bool(enabled),
            "base_url": _config["base_url"],
            "last_message": "AM Hub adapter configured.",
        }
    )

    if _config["enabled"] and _config["lsl_enabled"]:
        _initialize_lsl_outlets()

    if not _registered_shutdown:
        atexit.register(stop)
        _registered_shutdown = True

    if enabled:
        start()


def start() -> dict[str, Any]:
    """Open the AM Hub SSE connection and start publishing combined samples."""
    global _running, _generation, _reader_thread, _publisher_thread, _ping_thread, _hub_dropped_events

    if not _config:
        _set_state({"status": "not_configured", "last_message": "AM Hub adapter is not configured."})
        return get_status()
    if not _config.get("enabled"):
        _set_state({"status": "disabled", "last_message": "AM Hub is disabled in hardware settings."})
        return get_status()
    if not _config.get("base_url"):
        _set_state({"status": "waiting", "last_message": "AM Hub base URL is not configured."})
        return get_status()

    with _lock:
        if _running:
            return get_status()
        old_threads = [t for t in (_reader_thread, _publisher_thread, _ping_thread) if t is not None]
    # Let the previous run's threads finish first (they exit on the
    # generation change; stop() closes the stream, so the reader returns at
    # once, at the latest after its read timeout).
    for thread in old_threads:
        if thread is not threading.current_thread():
            thread.join(timeout=READ_TIMEOUT_SECONDS["v2"] + CONNECT_TIMEOUT_SECONDS)

    with _lock:
        if _running:
            return get_status()

        _running = True
        _generation += 1
        generation = _generation
        _stop_event.clear()
        # A new run starts with no samples from an earlier participant.
        _history.clear()
        with _topics_lock:
            _topics.clear()
        _hub_rtts.clear()
        _hub_boards.clear()
        _hub_host.clear()
        _frame_counts.clear()
        _frame_times.clear()
        _seq_gaps.clear()
        _last_seq.clear()
        _lost_baseline.clear()
        _hub_dropped_events = 0
        _clear_preview()
        _reset_link()
        _reader_thread = threading.Thread(target=_sse_loop, args=(generation,), daemon=True)
        _publisher_thread = threading.Thread(target=_publish_loop, args=(generation,), daemon=True)
        _ping_thread = threading.Thread(target=_ping_loop, args=(generation,), daemon=True)
        _reader_thread.start()
        _publisher_thread.start()
        _ping_thread.start()

    _set_state({"status": "connecting", "last_message": f"Connecting to the AM Hub at {_config.get('base_url')}."})
    return get_status()


def stop() -> dict[str, Any]:
    """Stop publishing and close the AM Hub SSE connection."""
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

    _set_state({"status": "stopped", "last_message": "AM Hub stream stopped."})
    return get_status()


def restart() -> dict[str, Any]:
    stop()
    return start()


def _alive(generation: int) -> bool:
    return _running and generation == _generation


def is_configured() -> bool:
    """Return True after initialize() stored AM Hub settings."""
    return bool(_config)


def set_auto_reconnect(enabled: bool) -> None:
    """The operator's auto-reconnect switch; the read loop checks it after each loss."""
    if _config:
        _config["auto_reconnect"] = bool(enabled)


def ingest_sample(payload: dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    """Ingest one combined AM Hub sample (one value per declared channel)."""
    sample = _normalize_sample(payload)
    sample["source"] = source
    sample["server_received_at"] = timestamp()
    sample["_epoch"] = time.time()
    _history.append(dict(sample))
    _update_preview(sample)

    # Only the sample. Whether the hub is connected and whether a person is
    # there is decided in get_status() from the link and the topics -- this
    # runs on a fixed tick even while the hub is unreachable.
    _set_state({"latest": sample})
    if _config.get("lsl_enabled"):
        _push_lsl_sample(sample)
    return sample


def set_recording(enabled: bool) -> None:
    """Track the active stimulus phase without gating continuous LSL output."""
    global _recording_enabled
    _recording_enabled = bool(enabled)
    _set_state(
        {
            "recording_enabled": _recording_enabled,
            "last_message": f"AM Hub recording {'enabled' if _recording_enabled else 'disabled'}.",
        }
    )


def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_latest_state)

    latest = dict(status.get("latest") or {})
    status["latest"] = latest
    status["enabled"] = bool(_config.get("enabled", False))
    status["lsl_enabled"] = bool(_config.get("lsl_enabled", False))
    status["recording_enabled"] = bool(_recording_enabled)
    status["base_url"] = _config.get("base_url", "")
    status["streams"] = list(_lsl_outlets.keys())
    status["auto_reconnect"] = bool(_config.get("auto_reconnect", True))
    status["api_version"] = _api_version
    status["hub_host"] = dict(_hub_host)
    status["hub_boards"] = {role: dict(info) for role, info in _hub_boards.items()}
    status["hub_rtt_ms"] = _hub_rtt_ms()
    status["data_quality"] = {
        "frames": dict(_frame_counts),
        "seq_gaps": dict(_seq_gaps),
        "hub_dropped_events": _hub_dropped_events,
    }
    with _preview_lock:
        status["preview"] = {key: list(points) for key, points in _preview.items()}

    now = time.time()
    # Freshness is judged by when a topic last actually updated, not by the
    # sample's own _epoch -- that is always "now", since combined samples
    # publish on a fixed tick regardless of whether the hub sent anything new.
    with _topics_lock:
        last_topic_epoch = _last_topic_update_epoch
        topics = {addr: dict(entry) for addr, entry in _topics.items()}
    if last_topic_epoch is not None:
        status["last_activity_epoch"] = last_topic_epoch
        status["last_activity_at"] = timestamp(last_topic_epoch)
        status["seconds_since_last_activity"] = round(max(0.0, now - last_topic_epoch), 3)

    person = _person(topics, now)
    status["person"] = person
    status["link"] = _link_status(now)
    status["boards"] = _board_status(now)
    # Everything the hub sent, as sent: raw value and age per topic address.
    status["topics"] = {
        addr: {"value": entry["value"], "age_s": round(max(0.0, now - entry["received_at"]), 2)}
        for addr, entry in sorted(topics.items())
        if not addr.startswith("hub:")
    }
    status["unknown_topics"] = sorted(
        addr for addr in topics if not addr.startswith("hub:") and addr not in TOPIC_CHANNELS
    )
    if _running:
        status.update(_link_derived_status(status["link"], person))

    # The live running state (not the configured one) and the shared
    # connection facts every sensor reports; the core derives ready/next step.
    status["running"] = bool(_running)
    status["connection"] = presence_sensor_connection(
        str(status.get("status") or ""),
        running=bool(_running),
        message=str(status.get("last_message") or ""),
        # The hub connects by itself when switched on, so the switch applies
        # at once; there is no manual connection to wait for.
        auto_reconnect=bool(_config.get("auto_reconnect", True)),
        had_connection=True,
        device_label=_device_label(),
    )
    return status


def _device_label() -> str:
    """The hub's address, API and how each board is attached (BLE / WiFi)."""
    url = str(_config.get("base_url") or "").split("://", 1)[-1]
    parts = [f"AM Hub {url}".strip()]
    if _api_version:
        parts.append(_api_version)
    boards = [
        f"{role} {str(info.get('transport') or '').upper() or '?'}"
        for role, info in sorted(_hub_boards.items())
        if info.get("connected")
    ]
    return " · ".join(parts + boards)


def get_interval_summary(start_epoch: float, end_epoch: float) -> dict[str, Any]:
    samples = _samples_in_interval(start_epoch, end_epoch)
    if not samples:
        return {
            "available": False,
            "sample_count": 0,
            "avg_presence": None,
            "avg_move_energy": None,
            "avg_person_distance": None,
            "avg_heart_rate": None,
            "avg_breath_rate": None,
            **truncation_info(_history, start_epoch),
        }

    return {
        "available": True,
        "sample_count": len(samples),
        "avg_presence": _mean(samples, "presence"),
        "avg_move_energy": _mean(samples, "moveEnergy"),
        "avg_person_distance": _mean(samples, "personDist"),
        "avg_heart_rate": _mean(samples, "heartRate"),
        "avg_breath_rate": _mean(samples, "breathRate"),
        "max_gap_seconds": max_gap_seconds(samples),
        **truncation_info(_history, start_epoch),
    }


def export_interval_samples(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    """Return raw-ish history samples for compact JSON sidecar export."""
    return [_public_sample(sample) for sample in _samples_in_interval(start_epoch, end_epoch)]


def _sse_loop(generation: int) -> None:
    """Hold one stream to the hub; replace a dead one at once.

    A stream counts as dead when the hub sends no byte for LINK_IDLE_SECONDS:
    the socket read times out and the reader returns. The next attempt
    follows immediately, then after 0.5 / 1 / 2 s.
    """
    global _running, _active_response, _api_version

    session = _stream_session()
    failures = 0
    prefer_v2 = True
    try:
        while _alive(generation):
            delay = RECONNECT_BACKOFF_SECONDS[min(failures, len(RECONNECT_BACKOFF_SECONDS) - 1)]
            if delay and _stop_event.wait(delay):
                break
            if not _alive(generation):
                break

            version = "v2" if prefer_v2 else "v1"
            prefer_v2 = True  # v1 is only this attempt's fallback; a hub may gain v2
            response = None
            reason = ""
            delivered = False
            _link_update(state="connecting", close_reason="")
            try:
                response = session.get(
                    f"{_config['base_url']}/api/{version}/stream",
                    params={"client": _client_id()},
                    stream=True,
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS[version]),
                )
                if version == "v2" and response.status_code == 404:
                    prefer_v2 = False  # older hub without /api/v2: try v1 right away
                    failures = 0
                    continue
                response.raise_for_status()
                with _lock:
                    if not _alive(generation):
                        break
                    _active_response = response
                _api_version = version
                _link_opened(version)
                delivered = _read_sse_events(response, generation)
                reason = "the hub closed the stream"
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
            with _link_lock:
                reason = _link.get("close_reason") or reason
            _link_lost(reason)
            failures = 0 if delivered else failures + 1
            if not _config.get("auto_reconnect", True):
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
                data_lines.append(line[len("data:"):].lstrip(" "))
            # "event:"/"id:"/"retry:" fields are unused -- the hub only sends
            # default "message" events, distinguished by their "type" field.
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
        # The watchdog in plain words: the hub fell silent.
        with _link_lock:
            api = _link.get("api") or "v2"
        return f"no data from the hub for {READ_TIMEOUT_SECONDS.get(api, READ_TIMEOUT_SECONDS['v2']):.0f} s"
    return text if len(text) <= 160 else text[:157] + "..."


def _link_update(**values: Any) -> None:
    with _link_lock:
        _link.update(values)


def _link_opened(version: str) -> None:
    now = time.time()
    with _link_lock:
        _link.update(state="open", opened_at=now, api=version, last_error="")
    _push_link_event("connected", "")


def _link_heard(at: float | None = None) -> None:
    moment = at if at is not None else time.time()
    with _link_lock:
        _link["last_event_at"] = moment
    _event_times.append(moment)


def _link_lost(reason: str) -> None:
    now = time.time()
    with _link_lock:
        was_open = _link.get("state") == "open"
        _link.update(state="lost", last_error=reason, close_reason="")
        if was_open:
            _link["reconnects"] = int(_link.get("reconnects") or 0) + 1
            _link["last_loss"] = {"at": timestamp(now), "epoch": now, "reason": reason}
    if was_open:
        _push_link_event("lost", reason)
        print(f"[AmHub] Connection lost ({reason}); reconnecting.")


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


def _link_status(now: float) -> dict[str, Any]:
    with _link_lock:
        link = dict(_link)
    recent = [moment for moment in list(_event_times) if now - moment <= 2.0]
    last_event = link.get("last_event_at")
    return {
        "state": link.get("state"),
        "api": link.get("api"),
        "connected_for_s": round(now - link["opened_at"], 1) if link.get("state") == "open" and link.get("opened_at") else None,
        "last_event_age_s": round(max(0.0, now - last_event), 2) if last_event else None,
        "events_per_s": round(len(recent) / 2.0, 1),
        "reconnects": int(link.get("reconnects") or 0),
        "last_loss": link.get("last_loss"),
        "last_error": link.get("last_error") or "",
    }


def _link_derived_status(link: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
    """The plain status from the link: connected only while events arrive."""
    state = link.get("state")
    age = link.get("last_event_age_s")
    limit = LINK_IDLE_SECONDS.get(link.get("api") or "v2", LINK_IDLE_SECONDS["v2"])
    if state == "open" and age is not None and age <= limit:
        if person["detected"]:
            return {"status": "connected", "last_message": "AM Hub connected; person detected."}
        return {"status": "no_presence", "last_message": "AM Hub connected; no person detected."}
    if link.get("last_event_age_s") is None:
        error = link.get("last_error")
        message = f"AM Hub not reachable: {error}" if error else f"Connecting to the AM Hub at {_config.get('base_url')}."
        return {"status": "connecting", "last_message": message}
    reason = link.get("last_error") or (link.get("last_loss") or {}).get("reason") or "no data"
    return {"status": "stale", "last_message": f"Connection to the AM Hub lost ({reason}); reconnecting."}


def _person(topics: dict[str, dict[str, Any]], now: float) -> dict[str, Any]:
    """Whether any fresh source sees a person, and which ones do."""

    def fresh(addr: str) -> float | None:
        entry = topics.get(addr)
        if entry is None or now - entry["received_at"] > TOPIC_STALE_SECONDS:
            return None
        return entry["value"]

    sources = []
    if (fresh(PERSON_FLAG_TOPIC) or 0) > 0:
        sources.append("presence")
    if (fresh(PERSON_TARGETS_TOPIC) or 0) >= 1:
        sources.append("position")
    if any((fresh(addr) or 0) > 0 for addr in PERSON_VITAL_TOPICS):
        sources.append("vitals")
    return {"detected": bool(sources), "sources": sources}


def _board_status(now: float) -> dict[str, Any]:
    """Per board: frames per second over the last 2 s and age of the last frame."""
    boards: dict[str, Any] = {}
    silent_after = float(_config.get("data_timeout_seconds", 5.0))
    for board, times in list(_frame_times.items()):
        moments = list(times)
        if not moments:
            continue
        age = max(0.0, now - moments[-1])
        recent = [moment for moment in moments if now - moment <= 2.0]
        boards[board] = {
            "rate_hz": round(len(recent) / 2.0, 1),
            "age_s": round(age, 2),
            "frames": _frame_counts.get(board, 0),
            "live": age <= silent_after,
        }
    return boards


def _push_link_event(state: str, reason: str) -> None:
    """Mark a connection change in the recorded event stream, so every gap is visible."""
    now = time.time()
    _push_raw_event(json.dumps(
        {"type": "link", "source": "study_runner", "state": state, "reason": reason,
         "client_time": now, "api": _link.get("api")},
        separators=(",", ":"),
    ))


def _push_raw_event(data: str) -> None:
    outlet = _lsl_outlets.get(EVENTS_STREAM.upper())
    if outlet is None:
        return
    try:
        outlet.push_sample([data])
    except Exception as error:
        print(f"[AmHub] Could not push a hub event to LSL: {error}")


def _handle_sse_event(data: str) -> None:
    # Freshness is always judged on this machine's clock: a value counts as
    # received when it arrives here. Hub timestamps only ever say how old a
    # value already was on arrival (hub clock minus hub clock).
    received = time.time()
    _link_heard(received)
    # Recorded first and verbatim, whatever it contains.
    _push_raw_event(data)
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return

    event_type = payload.get("type")
    if event_type == "frame":
        _handle_frame(payload, received)
    elif event_type in ("status", "hello"):
        devices = payload.get("devices") if event_type == "status" else payload.get("status")
        _handle_hub_status(devices, received)
        if event_type == "hello" and isinstance(payload.get("host"), dict):
            _hub_host.clear()
            _hub_host.update(payload["host"])
    elif event_type == "valves":
        _update_topic("hub:sceneActive", 1.0 if payload.get("scene_active") else 0.0, received)
    elif event_type == "scene":
        _update_topic("hub:sceneActive", 1.0 if payload.get("status") == "playing" else 0.0, received)
    elif event_type == "gap":
        _count_hub_dropped(int(payload.get("dropped") or 0))
    elif event_type == "init":
        hub_now = _to_float(payload.get("t"))
        for addr, info in (payload.get("topics") or {}).items():
            if not isinstance(info, dict):
                continue
            samples = info.get("samples") or []
            if samples:
                last_t, last_value = samples[-1]
                last_t = _to_float(last_t)
                age = max(0.0, hub_now - last_t) if hub_now is not None and last_t is not None else 0.0
                _update_topic(addr, last_value, received - age)
    elif event_type == "sample":
        addr = payload.get("addr")
        if isinstance(addr, str) and addr:
            # Every topic is kept, known or not: nothing the hub sends vanishes.
            _update_topic(addr, payload.get("value"), received)
    # "tick" (v1) and "valve_change" carry nothing the fixed tick needs.


def _handle_frame(payload: dict[str, Any], received: float) -> None:
    """One board packet: all its values update together (atomically for
    the 10 Hz tick)."""
    device = str(payload.get("device") or "")
    values = payload.get("values") or {}
    if not isinstance(values, dict):
        return
    seq = payload.get("seq")
    if isinstance(seq, int):
        previous = _last_seq.get(device)
        if previous is not None and seq > previous + 1:
            _seq_gaps[device] = _seq_gaps.get(device, 0) + seq - previous - 1
        _last_seq[device] = seq
    _frame_counts[device] = _frame_counts.get(device, 0) + 1
    # Per board by its role (radar, bio, solenoid), as the hub names it.
    board = str(payload.get("role") or device)
    _frame_times.setdefault(board, deque(maxlen=64)).append(received)
    # All values, known topics or not (for example the WiFi-only RSSI).
    _update_topics({str(addr): value for addr, value in values.items() if addr}, received)


def _handle_hub_status(devices: Any, received: float) -> None:
    """Per-board link state from the hub -> hub_status channels. Latency per
    board = radio round trip / 2 + hub-internal delay + this adapter's round
    trip to the hub / 2 - each part measured without comparing clocks."""
    if not isinstance(devices, dict):
        return
    hub_rtt = _hub_rtt_ms()
    updates: dict[str, Any] = {"hub:hubRttMs": hub_rtt, "hub:hubDroppedEvents": _hub_dropped_events}
    for info in devices.values():
        if not isinstance(info, dict) or info.get("role") not in HUB_ROLES:
            continue
        role = info["role"]
        lost = _lost_this_session(role, _to_float(info.get("gap_count")))
        link_rtt = _to_float(info.get("link_rtt_ms"))
        hub_latency = _to_float(info.get("hub_latency_ms"))
        latency = None
        if info.get("connected") and link_rtt is not None and hub_latency is not None and hub_rtt is not None:
            latency = link_rtt / 2 + hub_latency + hub_rtt / 2
        _hub_boards[role] = {
            "connected": bool(info.get("connected")), "transport": info.get("transport"),
            "rssi": info.get("rssi"), "rate_hz": info.get("rate_hz"), "lost": lost,
            "link_rtt_ms": link_rtt, "hub_latency_ms": hub_latency,
            "latency_ms": round(latency, 2) if latency is not None else None,
            "detail": info.get("detail"),
        }
        updates.update({
            f"hub:{role}Connected": 1.0 if info.get("connected") else 0.0,
            f"hub:{role}Transport": TRANSPORT_CODES.get(info.get("transport")),
            f"hub:{role}Rssi": info.get("rssi"),
            f"hub:{role}RateHz": info.get("rate_hz"),
            f"hub:{role}Lost": lost,
            f"hub:{role}LinkRttMs": link_rtt,
            f"hub:{role}LatencyMs": latency,
        })
    _update_topics(updates, received)


def _lost_this_session(role: str, gap_count: float | None) -> float | None:
    """The hub counts lost packets since it started; a session only sees the
    ones lost since its own start(). A smaller count means the hub restarted."""
    if gap_count is None:
        return None
    baseline = _lost_baseline.setdefault(role, gap_count)
    if gap_count < baseline:
        baseline = _lost_baseline[role] = 0.0
    return gap_count - baseline


def _count_hub_dropped(count: int) -> None:
    global _hub_dropped_events
    _hub_dropped_events += max(0, count)


def _hub_rtt_ms() -> float | None:
    values = sorted(_hub_rtts)
    return round(values[len(values) // 2], 2) if values else None


def _ping_loop(generation: int) -> None:
    """Measures this adapter's HTTP round trip to the hub once per second,
    on its own monotonic clock. Hubs without /api/v2 are skipped."""
    import requests

    session = requests.Session()
    while _alive(generation):
        if _api_version == "v2":
            started = time.perf_counter()
            try:
                response = session.get(f"{_config['base_url']}/api/v2/ping", timeout=2.0)
                ok = response.ok
            except Exception:
                ok = False
            if ok:
                _hub_rtts.append((time.perf_counter() - started) * 1000.0)
            else:
                _hub_rtts.clear()  # an old round trip must not stand in for a failing one
        _stop_event.wait(PING_INTERVAL_SECONDS)
    session.close()


def _update_topic(addr: str, value: Any, received_at: Any) -> None:
    _update_topics({addr: value}, received_at)


def _update_topics(values: dict[str, Any], received_at: Any) -> None:
    """Update several topics under one lock, so the 10 Hz tick sees a board
    packet either completely or not at all. received_at: local epoch."""
    global _last_topic_update_epoch

    if not values:
        return
    resolved_received_at = _to_float(received_at) or time.time()
    with _topics_lock:
        for addr, value in values.items():
            _topics[addr] = {"value": _to_float(value), "received_at": resolved_received_at}
        if any(not addr.startswith("hub:") for addr in values):
            _last_topic_update_epoch = resolved_received_at


def _publish_loop(generation: int | None = None) -> None:
    interval = 1.0 / PUBLISH_RATE_HZ
    next_tick = time.monotonic()
    while _running and (generation is None or generation == _generation):
        next_tick += interval
        _publish_combined_sample()
        remaining = next_tick - time.monotonic()
        if remaining > 0:
            _stop_event.wait(remaining)
        else:
            next_tick = time.monotonic()


def _publish_combined_sample() -> None:
    now = time.time()
    with _topics_lock:
        snapshot = dict(_topics)

    payload: dict[str, Any] = {}
    for channel, topic in CHANNEL_TOPICS.items():
        entry = snapshot.get(topic)
        if entry is None or now - entry["received_at"] > TOPIC_STALE_SECONDS:
            payload[channel] = None
        else:
            payload[channel] = entry["value"]
    ingest_sample(_hub_values_to_contract(payload), source="am_hub")


def _hub_values_to_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """The one place hub values become contract values: firmware units ->
    mm / mm/s, and the firmware's 0-for-no-value -> missing (NaN in LSL)."""
    values = dict(payload)
    for channel, factor in CHANNEL_SCALE.items():
        if values.get(channel) is not None:
            values[channel] = values[channel] * factor
    for channel in ZERO_MEANS_MISSING:
        if values.get(channel) == 0:
            values[channel] = None
    for x_channel, y_channel, group in EMPTY_AT_ORIGIN:
        if values.get(x_channel) == 0 and values.get(y_channel) == 0:
            values.update(dict.fromkeys(group))
    if values.get("targetCount") == 0:
        values.update(dict.fromkeys(NEAREST_TARGET_CHANNELS))
    return values


def _clear_preview() -> None:
    global _last_preview_epoch
    with _preview_lock:
        for points in _preview.values():
            points.clear()
        _last_preview_epoch = 0.0


def _update_preview(sample: dict[str, Any]) -> None:
    global _last_preview_epoch

    epoch = float(sample["_epoch"])
    if epoch - _last_preview_epoch < 1.0:
        return
    _last_preview_epoch = epoch

    groups = {
        "movement": {name: sample.get(name) for name in TREND_MOVEMENT_CHANNELS},
        "position": {name: sample.get(name) for name in TREND_POSITION_CHANNELS},
        "vitals": {name: sample.get(name) for name in TREND_VITALS_CHANNELS},
    }
    with _preview_lock:
        for key, values in groups.items():
            _preview[key].append(
                {
                    "at": epoch,
                    "received_at": epoch,
                    "values": values,
                    "validity": "valid" if any(value is not None for value in values.values()) else "uncertain",
                }
            )


def _normalize_sample(payload: dict[str, Any]) -> dict[str, Any]:
    return {channel: _to_float(payload.get(channel)) for channel in CHANNEL_TOPICS}


def _initialize_lsl_outlets() -> None:
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

    def create_outlet(suffix: str) -> Any:
        labels = STREAM_CHANNELS[suffix]
        units = LSL_CHANNEL_UNITS[suffix]
        info = StreamInfo(
            name=f"{prefix}_{suffix.upper()}",
            type=suffix.upper(),
            channel_count=len(labels),
            nominal_srate=PUBLISH_RATE_HZ,
            channel_format="float32",
            source_id=LSL_SOURCE_IDS[suffix],
        )
        channels = info.desc().append_child("channels")
        for label, unit in zip(labels, units, strict=True):
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
            channel.append_child_value("unit", unit)
        apply_stream_contract_desc(info, STREAM_CONTRACTS[suffix])
        return StreamOutlet(info)

    def create_events_outlet() -> Any:
        info = StreamInfo(
            name=f"{prefix}_{EVENTS_STREAM.upper()}",
            type=EVENTS_STREAM.upper(),
            channel_count=1,
            nominal_srate=0.0,  # irregular: one sample per hub event
            channel_format="string",
            source_id=LSL_SOURCE_IDS[EVENTS_STREAM],
        )
        channel = info.desc().append_child("channels").append_child("channel")
        channel.append_child_value("label", "event")
        channel.append_child_value("unit", LSL_CHANNEL_UNITS[EVENTS_STREAM][0])
        apply_stream_contract_desc(info, STREAM_CONTRACTS[EVENTS_STREAM])
        return StreamOutlet(info)

    _lsl_outlets = {key.upper(): create_outlet(key) for key in STREAM_CHANNELS}
    _lsl_outlets[EVENTS_STREAM.upper()] = create_events_outlet()
    print("[AmHub] LSL outlets ready.")


def _push_lsl_sample(sample: dict[str, Any]) -> None:
    if not _lsl_outlets:
        return

    for key, channels in STREAM_CHANNELS.items():
        _push_lsl_values(key.upper(), sample, channels)


def _push_lsl_values(stream_key: str, sample: dict[str, Any], fields: tuple[str, ...]) -> None:
    outlet = _lsl_outlets.get(stream_key)
    if outlet is None:
        return

    values = []
    for field in fields:
        value = sample.get(field)
        # NaN, not 0.0: a topic that never reported would otherwise read as
        # a real zero reading (see docs/sensors-and-data.md on missing values).
        values.append(float(value) if value is not None else float("nan"))

    try:
        outlet.push_sample(values)
    except Exception as error:
        print(f"[AmHub] Could not push {stream_key} sample to LSL: {error}")


def _set_state(values: dict[str, Any]) -> None:
    set_state(_latest_state, _state_lock, values)


def _samples_in_interval(start_epoch: float, end_epoch: float) -> list[dict[str, Any]]:
    return samples_in_interval(_history, start_epoch, end_epoch)


def _public_sample(sample: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key, value in sample.items():
        if key == "_epoch":
            public["server_received_epoch"] = value
        elif not key.startswith("_"):
            public[key] = value
    return public


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(samples: list[dict[str, Any]], key: str) -> float | None:
    values = [float(sample[key]) for sample in samples if sample.get(key) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)
