"""AM Hub adapter for presence, position, movement (and future vitals) data.

The AM Hub is a Raspberry Pi service that controls the Parasite autonomous
material and exposes its sensor readings over a small, unauthenticated
HTTP/SSE "v1" API. This adapter is a client of that API: it opens a
long-lived Server-Sent-Events connection to ``{base_url}/api/v1/stream``,
caches the latest value per OSC-style topic address, and republishes a
combined sample per declared stream (presence/position/vitals) on a fixed
10 Hz tick -- matching the underlying board's own sample rate, since the
hub pushes per-topic events rather than one combined row.

Heart rate and breathing ("vitals") are not exposed by the hub yet; those
channels stay ``NaN`` until its own AM Hub extension starts forwarding the
matching topics (see the plugin's README for the exact topic names).
"""
from __future__ import annotations

import atexit
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
    # Not yet forwarded by the hub -- stay NaN until its bio_hub extension ships.
    "heartRate": "/sensor/heartBpm",
    "breathRate": "/sensor/breathRate",
    "bioDistance": "/sensor/bioDist",
    "bioX": "/sensor/bioT1x",
    "bioY": "/sensor/bioT1y",
}
TOPIC_CHANNELS: dict[str, str] = {topic: channel for channel, topic in CHANNEL_TOPICS.items()}
LSL_SOURCE_IDS = {
    "presence": "study_runner.am_hub.presence",
    "position": "study_runner.am_hub.position",
    "vitals": "study_runner.am_hub.vitals",
}
LSL_CHANNEL_UNITS: dict[str, tuple[str, ...]] = {
    "presence": ("boolean", "enum", "millimetre", "arbitrary_unit", "arbitrary_unit", "count"),
    "position": (
        "millimetre", "millimetre", "millimetre",
        "millimetre", "millimetre", "millimetre_per_second",
        "millimetre", "millimetre", "millimetre_per_second",
        "millimetre", "millimetre", "millimetre_per_second",
    ),
    "vitals": ("beats_per_minute", "breaths_per_minute", "millimetre", "millimetre", "millimetre"),
}
# Dashboard trend-graph channels -- a subset of STREAM_CHANNELS, matching
# ui/dashboard.js's TREND_CONFIG.
TREND_MOVEMENT_CHANNELS: tuple[str, ...] = ("moveEnergy", "staticEnergy")
TREND_POSITION_CHANNELS: tuple[str, ...] = ("personX", "personY")
PUBLISH_RATE_HZ = 10.0
# The combined sample publishes on a fixed tick regardless of whether the hub
# sent anything new -- a topic older than this is republished as missing
# (None -> NaN) rather than silently carried forward as if it were live.
# Matches manifest.json's capabilities.backup_projection.stale_after_ms.
TOPIC_STALE_SECONDS = 2.5
# Package 5d (docs/archive/architecture-1.0-umbau.md): this plugin's own frozen
# stream contract, for the desc/study_runner XDF header block only.
STREAM_CONTRACTS = load_own_stream_contracts(__file__)

_lock = threading.Lock()
_state_lock = threading.Lock()
_topics_lock = threading.Lock()
_preview_lock = threading.Lock()
_config: dict[str, Any] = {}
_running = False
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
# Dashboard trend graphs: a bounded, ~1 Hz preview per graph, same shape and
# cadence as the BrainBit dashboard's preview (at/received_at/values/validity)
# so its ui/dashboard.js auto-scale/gap logic can be ported unchanged.
_preview: dict[str, deque[dict[str, Any]]] = {"movement": deque(maxlen=60), "position": deque(maxlen=60)}
_last_preview_epoch = 0.0
# AM Hub reports at ~10 Hz; sized to hold a full study session.
_history: deque[dict[str, Any]] = deque(maxlen=history_maxlen(PUBLISH_RATE_HZ))
_latest_state: dict[str, Any] = {
    "status": "not_configured",
    "latest": {},
    "last_message": "AM Hub adapter has not been configured.",
}


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
    global _running, _reader_thread, _publisher_thread

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

        _running = True
        _stop_event.clear()
        # A new run starts with no samples from an earlier participant.
        _history.clear()
        with _topics_lock:
            _topics.clear()
        _reader_thread = threading.Thread(target=_sse_loop, daemon=True)
        _publisher_thread = threading.Thread(target=_publish_loop, daemon=True)
        _reader_thread.start()
        _publisher_thread.start()

    _set_state({"status": "starting", "last_message": "AM Hub stream starting."})
    return get_status()


def stop() -> dict[str, Any]:
    """Stop publishing and close the AM Hub SSE connection."""
    global _running

    with _lock:
        _running = False
        _stop_event.set()
        if _active_response is not None:
            with contextlib.suppress(Exception):
                _active_response.close()

    _set_state({"status": "stopped", "last_message": "AM Hub stream stopped."})
    return get_status()


def restart() -> dict[str, Any]:
    stop()
    return start()


def is_configured() -> bool:
    """Return True after initialize() stored AM Hub settings."""
    return bool(_config)


def ingest_sample(payload: dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    """Ingest one combined AM Hub sample (one value per declared channel)."""
    sample = _normalize_sample(payload)
    sample["source"] = source
    sample["server_received_at"] = timestamp()
    sample["_epoch"] = time.time()
    _history.append(dict(sample))
    _update_preview(sample)

    _set_state(
        {
            "status": "connected" if sample.get("presence") else "no_presence",
            "latest": sample,
            "last_activity_at": sample["server_received_at"],
            "last_activity_epoch": sample["_epoch"],
            "last_message": "AM Hub sample received.",
        }
    )
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
    with _preview_lock:
        status["preview"] = {key: list(points) for key, points in _preview.items()}

    # Freshness is judged by when a topic last actually updated, not by the
    # sample's own _epoch -- that is always "now", since combined samples
    # publish on a fixed tick regardless of whether the hub sent anything new.
    with _topics_lock:
        last_topic_epoch = _last_topic_update_epoch
    if last_topic_epoch is not None:
        status["last_activity_epoch"] = last_topic_epoch
        status["last_activity_at"] = timestamp(last_topic_epoch)
        age = max(0.0, time.time() - last_topic_epoch)
        status["seconds_since_last_activity"] = round(age, 3)
        timeout = float(_config.get("data_timeout_seconds", 5.0))
        if _running and age > timeout and status.get("status") in {"connected", "no_presence", "starting"}:
            status["status"] = "stale"
            status["last_message"] = f"No AM Hub data for {age:.1f}s."

    return status


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


def _sse_loop() -> None:
    global _running, _active_response

    last_attempt = 0.0
    while _running:
        now = time.time()
        delay = float(_config.get("reconnect_delay_seconds", 3.0))
        if now - last_attempt < delay:
            _stop_event.wait(0.2)
            continue
        last_attempt = now

        import requests

        response = None
        try:
            _set_state({"status": "waiting", "last_message": f"Connecting to AM Hub at {_config.get('base_url')}."})
            response = requests.get(f"{_config['base_url']}/api/v1/stream", stream=True, timeout=(5, None))
            response.raise_for_status()
            _active_response = response
            _set_state({"status": "starting", "last_message": "AM Hub stream connected, waiting for data."})
            _read_sse_events(response)
        except Exception as error:
            if _running:
                _set_state({"status": "waiting", "last_message": f"AM Hub connection failed: {error}"})
        finally:
            _active_response = None
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()

        if not _config.get("auto_reconnect", True):
            break

    with _lock:
        if _reader_thread is threading.current_thread():
            _running = False


def _read_sse_events(response: Any) -> None:
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if not _running:
            break
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                _handle_sse_event("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith(":"):
            continue  # SSE comment/heartbeat line.
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        # "event:"/"id:"/"retry:" fields are unused -- the hub only sends
        # default "message" events (init/sample/tick, distinguished by
        # their own "type" field in the JSON payload).


def _handle_sse_event(data: str) -> None:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return

    event_type = payload.get("type")
    if event_type == "init":
        for addr, info in (payload.get("topics") or {}).items():
            if addr not in TOPIC_CHANNELS or not isinstance(info, dict):
                continue
            samples = info.get("samples") or []
            if samples:
                last_t, last_value = samples[-1]
                _update_topic(addr, last_value, last_t)
    elif event_type == "sample":
        addr = payload.get("addr")
        if addr in TOPIC_CHANNELS:
            _update_topic(addr, payload.get("value"), payload.get("t"))
    # "tick" events carry only rate/staleness/dropout info, no value -- not
    # needed for sample assembly; staleness is derived locally in get_status().


def _update_topic(addr: str, value: Any, received_at: Any) -> None:
    global _last_topic_update_epoch

    resolved_received_at = _to_float(received_at) or time.time()
    with _topics_lock:
        _topics[addr] = {"value": _to_float(value), "received_at": resolved_received_at}
        _last_topic_update_epoch = resolved_received_at


def _publish_loop() -> None:
    interval = 1.0 / PUBLISH_RATE_HZ
    next_tick = time.monotonic()
    while _running:
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
    ingest_sample(payload, source="am_hub")


def _update_preview(sample: dict[str, Any]) -> None:
    global _last_preview_epoch

    epoch = float(sample["_epoch"])
    if epoch - _last_preview_epoch < 1.0:
        return
    _last_preview_epoch = epoch

    groups = {
        "movement": {name: sample.get(name) for name in TREND_MOVEMENT_CHANNELS},
        "position": {name: sample.get(name) for name in TREND_POSITION_CHANNELS},
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

    _lsl_outlets = {
        "PRESENCE": create_outlet("presence"),
        "POSITION": create_outlet("position"),
        "VITALS": create_outlet("vitals"),
    }
    print("[AmHub] LSL outlets ready.")


def _push_lsl_sample(sample: dict[str, Any]) -> None:
    if not _lsl_outlets:
        return

    _push_lsl_values("PRESENCE", sample, STREAM_CHANNELS["presence"])
    _push_lsl_values("POSITION", sample, STREAM_CHANNELS["position"])
    _push_lsl_values("VITALS", sample, STREAM_CHANNELS["vitals"])


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
