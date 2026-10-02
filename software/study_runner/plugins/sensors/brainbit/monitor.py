"""Connection-scoped operator facts; the graphs are the core's live view of the recorded metrics."""
from __future__ import annotations

from copy import deepcopy
import math
import threading
import time
import uuid


class BrainBitMonitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.connection_id = None
        self.connection_state = "stopped"
        self.last_event = None
        self.facts = {}
        self.low_battery = False
        self.battery_seen = None
        self.started = time.monotonic()
        self.artifact_started = None
        self.artifact_seconds = 0.0

    def observe(self, tag, payload, *, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            states = {"SCANNING": "searching", "DEVICE_SELECTED": "device_found",
                      "SELECTION_REQUIRED": "selection_required", "CONNECTING": "connecting",
                      "CONNECTED": "connected", "WAITING": "reconnecting", "DISCONNECTED": "reconnecting",
                      "CONNECT_FAILED": "reconnecting", "STOPPED": "stopped",
                      "SETUP_FAIL": "failed", "BLE_UNAVAILABLE": "failed"}
            if tag in {"CONNECTING", "WAITING", "STOPPED", "SCANNING", "DISCONNECTED"}:
                self.facts = {"CLOCK": self.facts["CLOCK"]} if "CLOCK" in self.facts else {}
                self.low_battery = False
                self.battery_seen = None
                self.artifact_started = None
                self.artifact_seconds = 0.0
                self.started = now
            if tag == "CONNECTING":
                self.connection_id = uuid.uuid4().hex
            if tag in states:
                self.connection_state = states[tag]
                self.last_event = {"tag": tag, "at": time.time(), "payload": deepcopy(payload)}
            if tag in {"DEVICE", "CONNECTED", "RESIST", "QUALITY", "CALIB", "ARTIFACT",
                       "EMO_INIT", "EMO_INIT_FAIL", "DERIVED_DISABLED", "CHANNEL_MAP",
                       "DATA_WARNING", "BATTERY", "CLOCK"}:
                value = deepcopy(payload)
                value["observed_at"] = time.time()
                if tag == "DEVICE" or (tag == "CALIB" and payload.get("event") not in {"START", "RESET"}):
                    value = {**self.facts.get(tag, {}), **value}
                self.facts[tag] = value
            if tag == "BATTERY":
                percent = payload.get("percent")
                if isinstance(percent, (int, float)) and math.isfinite(percent) and 0 <= percent <= 100:
                    self.battery_seen = now
                    if percent <= 20:
                        self.low_battery = True
                    elif percent >= 25:
                        self.low_battery = False
                else:
                    self.battery_seen = None
            if tag == "ARTIFACT":
                active = bool(payload.get("both_now") or payload.get("sequence"))
                if active and self.artifact_started is None:
                    self.artifact_started = now
                elif not active and self.artifact_started is not None:
                    self.artifact_seconds += now - self.artifact_started
                    self.artifact_started = None

    def snapshot(self, *, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            age = None if self.battery_seen is None else max(0, now - self.battery_seen)
            seconds = self.artifact_seconds + (now - self.artifact_started if self.artifact_started is not None else 0)
            return deepcopy({
                "connection_id": self.connection_id, "connection_state": self.connection_state,
                "last_event": self.last_event, "diagnostic_state": self.facts,
                "battery_age_seconds": age, "battery_stale": age is None or age > 120,
                "low_battery": self.low_battery and age is not None and age <= 120,
                "artifact_fraction": seconds / max(1, now - self.started) if "ARTIFACT" in self.facts else None,
            })
