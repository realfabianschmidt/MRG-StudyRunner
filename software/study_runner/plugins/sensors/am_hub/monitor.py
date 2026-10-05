"""What the dashboard shows about the AM Hub -- never what is recorded.

Like ``brainbit/monitor.py``: the adapter hands every hub event to
``AmHubMonitor.observe`` and the dashboard reads ``snapshot``. Everything here
is bounded, derived from events that actually arrived, and cleared by
``reset`` whenever the adapter starts or stops, so no value outlives its
connection or reaches the next session. The trend graphs are the core's live
view (manifest ``live_view``), fed by the recorded samples themselves.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import threading
from typing import Any

# Board frames name their values by OSC address; the dashboard uses the last
# path segment (the same name as the recorded channel).
PERSON_FLAG = "presence"
PERSON_TARGETS = "targetCount"
PERSON_VITALS = ("bioDist", "heartBpm", "breathRate")
# A value older than this no longer counts as "now" for person detection.
PERSON_STALE_SECONDS = 2.5
HUB_ROLES = ("radar", "bio", "solenoid")
# A board-status reply older than this no longer answers for the board's
# *current* radio round trip (adapter.py uses this to gate latency_ms).
STATUS_STALE_SECONDS = 2.0


def channel_name(address: str) -> str:
    """``/sensor/heartBpm`` -> ``heartBpm``: the hub's own name, without its path."""
    return str(address).rstrip("/").rsplit("/", 1)[-1]


class AmHubMonitor:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Forget everything; called on every start and stop."""
        with self.lock:
            self.values: dict[str, dict[str, Any]] = {}   # address -> {value, received_at}
            self.frame_times: dict[str, deque[float]] = {}
            self.frame_counts: dict[str, int] = {}
            self.last_seq: dict[str, int] = {}
            self.seq_gaps: dict[str, int] = {}
            self.hub_boards: dict[str, dict[str, Any]] = {}
            self.lost_baseline: dict[str, float] = {}
            self.hub_host: dict[str, Any] = {}
            self.hub_dropped_events = 0
            self.scene_active: bool | None = None
            self.last_frame_at: float | None = None

    # ------------------------------------------------------------ observe

    def observe(self, event: dict[str, Any], received: float) -> None:
        """Take one parsed hub event. ``received`` is this computer's clock."""
        kind = event.get("type")
        with self.lock:
            if kind == "frame":
                self._frame(event, received)
            elif kind in ("status", "hello"):
                self._boards(event.get("devices") if kind == "status" else event.get("status"), received)
                if kind == "hello" and isinstance(event.get("host"), dict):
                    self.hub_host = dict(event["host"])
            elif kind == "valves":
                self.scene_active = bool(event.get("scene_active"))
            elif kind == "scene":
                self.scene_active = event.get("status") == "playing"
            elif kind == "gap":
                self.hub_dropped_events += max(0, int(event.get("dropped") or 0))

    def _frame(self, event: dict[str, Any], received: float) -> None:
        values = event.get("values")
        if not isinstance(values, dict):
            return
        device = str(event.get("device") or "")
        board = str(event.get("role") or device)
        seq = event.get("seq")
        if isinstance(seq, int):
            previous = self.last_seq.get(board)
            if previous is not None and seq > previous + 1:
                self.seq_gaps[board] = self.seq_gaps.get(board, 0) + seq - previous - 1
            self.last_seq[board] = seq
        self.frame_counts[board] = self.frame_counts.get(board, 0) + 1
        self.frame_times.setdefault(board, deque(maxlen=64)).append(received)
        self.last_frame_at = received
        for address, value in values.items():
            self.values[str(address)] = {"value": _number(value), "received_at": received}

    def _boards(self, devices: Any, received: float) -> None:
        if not isinstance(devices, dict):
            return
        for info in devices.values():
            if not isinstance(info, dict) or info.get("role") not in HUB_ROLES:
                continue
            role = info["role"]
            self.hub_boards[role] = {
                "connected": bool(info.get("connected")),
                "transport": info.get("transport"),
                "rssi": info.get("rssi"),
                "rate_hz": info.get("rate_hz"),
                "lost": self._lost_this_session(role, _number(info.get("gap_count"))),
                "link_rtt_ms": _number(info.get("link_rtt_ms")),
                "hub_latency_ms": _number(info.get("hub_latency_ms")),
                "detail": info.get("detail"),
                "status_at": received,
            }

    def link_rtt_ms(self, role: str, now: float | None = None) -> float | None:
        """The radio round trip hub <-> board the hub measured last.

        ``None`` when unknown, the board is not connected, or (with ``now``
        given) the status that reported it is older than
        ``STATUS_STALE_SECONDS`` - a stale reply must not stand in for a
        current radio RTT.
        """
        with self.lock:
            board = self.hub_boards.get(role) or {}
            if not board.get("connected"):
                return None
            rtt = board.get("link_rtt_ms")
            if rtt is None:
                return None
            status_at = board.get("status_at")
            if now is not None and status_at is not None and now - status_at > STATUS_STALE_SECONDS:
                return None
            return rtt

    def _lost_this_session(self, role: str, gap_count: float | None) -> float | None:
        """The hub counts lost packets since it started; show only this connection's."""
        if gap_count is None:
            return None
        baseline = self.lost_baseline.setdefault(role, gap_count)
        if gap_count < baseline:  # the hub restarted
            baseline = self.lost_baseline[role] = 0.0
        return gap_count - baseline

    # ----------------------------------------------------------- snapshot

    def snapshot(self, now: float, *, fresh_seconds: float, hub_rtt_ms: float | None) -> dict[str, Any]:
        """Everything the dashboard needs, judged on this computer's clock."""
        with self.lock:
            fresh = {
                address: entry["value"] for address, entry in self.values.items()
                if now - entry["received_at"] <= fresh_seconds
            }
            boards = {}
            for board, times in self.frame_times.items():
                if not times:
                    continue
                age = max(0.0, now - times[-1])
                boards[board] = {
                    "rate_hz": round(sum(1 for moment in times if now - moment <= 2.0) / 2.0, 1),
                    "age_s": round(age, 2),
                    "frames": self.frame_counts.get(board, 0),
                    "live": age <= fresh_seconds,
                }
            # The latency per board is measured per frame by the adapter.
            hub_boards = {role: dict(info) for role, info in self.hub_boards.items()}
            return deepcopy({
                "latest": {channel_name(address): value for address, value in fresh.items()},
                "topics": {
                    address: {"value": entry["value"], "age_s": round(max(0.0, now - entry["received_at"]), 2)}
                    for address, entry in sorted(self.values.items())
                },
                "person": self._person(now),
                "boards": boards,
                "hub_boards": hub_boards,
                "hub_host": self.hub_host,
                "hub_rtt_ms": hub_rtt_ms,
                "scene_active": self.scene_active,
                "data_quality": {
                    "frames": dict(self.frame_counts),
                    "seq_gaps": dict(self.seq_gaps),
                    "hub_dropped_events": self.hub_dropped_events,
                },
                "last_frame_at": self.last_frame_at,
            })

    def boards_ready(self, now: float, fresh_seconds: float, roles: tuple[str, ...] = ("radar", "bio")) -> list[str]:
        """The measuring boards without a fresh frame (connected by the hub or not)."""
        with self.lock:
            missing = []
            for role in roles:
                times = self.frame_times.get(role)
                silent = not times or now - times[-1] > fresh_seconds
                if silent or self.hub_boards.get(role, {}).get("connected") is False:
                    missing.append(role)
            return missing

    def _person(self, now: float) -> dict[str, Any]:
        """Whether any fresh source sees a person, and which ones do."""
        by_name = {
            channel_name(address): entry["value"] for address, entry in self.values.items()
            if now - entry["received_at"] <= PERSON_STALE_SECONDS
        }
        sources = []
        if (by_name.get(PERSON_FLAG) or 0) > 0:
            sources.append("presence")
        if (by_name.get(PERSON_TARGETS) or 0) >= 1:
            sources.append("position")
        if any((by_name.get(name) or 0) > 0 for name in PERSON_VITALS):
            sources.append("vitals")
        return {"detected": bool(sources), "sources": sources}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
