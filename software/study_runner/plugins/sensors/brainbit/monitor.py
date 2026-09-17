"""Connection-scoped operator facts and a bounded, non-recording preview."""
from __future__ import annotations

from collections import deque
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
        self.preview = {key: deque(maxlen=60) for key in ("bands", "mental")}
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
                for points in self.preview.values():
                    points.clear()
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
            if tag in {"BANDS_BATCH", "MENTAL_BATCH"}:
                points = self.preview[tag.split("_")[0].lower()]
                stamps, samples = payload.get("timestamps", []), payload.get("samples", [])
                if stamps and samples and len(stamps) == len(samples):
                    point = {"at": stamps[-1], "received_at": time.time(),
                             "connection_id": self.connection_id,
                             "values": dict(zip(payload.get("channels", []), samples[-1])),
                             "validity": payload.get("validity", "unknown")}
                    if not points or point["at"] - points[-1]["at"] >= 1:
                        points.append(point)
                    elif point["validity"] != "valid":
                        # An invalid window must not disappear in the 1 Hz preview.
                        points[-1]["validity"] = point["validity"]
            if tag in {"CALIB", "ARTIFACT", "DERIVED_DISABLED", "EMO_INIT_FAIL"}:
                # Break any previously valid segment immediately, before new metrics arrive.
                for points in self.preview.values():
                    if points and (tag != "ARTIFACT" or payload.get("both_now") or payload.get("sequence")):
                        points[-1]["validity"] = "uncertain"

    def snapshot(self, *, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            age = None if self.battery_seen is None else max(0, now - self.battery_seen)
            seconds = self.artifact_seconds + (now - self.artifact_started if self.artifact_started is not None else 0)
            return deepcopy({
                "connection_id": self.connection_id, "connection_state": self.connection_state,
                "last_event": self.last_event, "diagnostic_state": self.facts,
                "preview": {key: list(points) for key, points in self.preview.items()},
                "battery_age_seconds": age, "battery_stale": age is None or age > 120,
                "low_battery": self.low_battery and age is not None and age <= 120,
                "artifact_fraction": seconds / max(1, now - self.started) if "ARTIFACT" in self.facts else None,
            })
