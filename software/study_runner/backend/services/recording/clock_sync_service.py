from __future__ import annotations

import math
import threading
import time
from collections import deque
from statistics import median
from typing import Any, Callable


class ClockSyncService:
    """Stores bounded offset/RTT histories for tablets and remote workers."""

    def __init__(
        self,
        *,
        max_samples_per_source: int = 24,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_samples_per_source = max(1, int(max_samples_per_source))
        self._clock = clock
        self._lock = threading.Lock()
        self._samples: dict[str, deque[dict[str, Any]]] = {}

    def record_server_exchange(
        self,
        *,
        source_id: str,
        source_type: str,
        client_send_ms: Any,
        server_receive_ms: float,
        server_send_ms: float,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        normalized_source_id = _normalize_source_id(source_id)
        if not normalized_source_id:
            return None
        sample = {
            "source_id": normalized_source_id,
            "source_type": str(source_type or "unknown"),
            "observed_at_epoch_ms": self._epoch_ms(),
            "client_send_ms": _finite_float(client_send_ms),
            "server_receive_ms": round(float(server_receive_ms), 3),
            "server_send_ms": round(float(server_send_ms), 3),
            "server_processing_ms": round(max(0.0, float(server_send_ms) - float(server_receive_ms)), 3),
            "metadata": dict(metadata or {}),
        }
        return self._append_sample(normalized_source_id, sample)

    def record_offset_sample(
        self,
        *,
        source_id: str,
        source_type: str,
        offset_ms: Any,
        rtt_ms: Any = None,
        sequence_number: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        normalized_source_id = _normalize_source_id(source_id)
        offset = _finite_float(offset_ms)
        if not normalized_source_id or offset is None:
            return None
        sample = {
            "source_id": normalized_source_id,
            "source_type": str(source_type or "unknown"),
            "observed_at_epoch_ms": self._epoch_ms(),
            "offset_ms": round(offset, 3),
            "rtt_ms": _rounded_optional_float(rtt_ms),
            "sequence_number": sequence_number,
            "metadata": dict(metadata or {}),
        }
        return self._append_sample(normalized_source_id, sample)

    def source_summary(self, source_id: str) -> dict[str, Any] | None:
        normalized_source_id = _normalize_source_id(source_id)
        if not normalized_source_id:
            return None
        with self._lock:
            samples = list(self._samples.get(normalized_source_id) or [])
        if not samples:
            return None
        return _summarize_source(normalized_source_id, samples)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            samples_by_source = {source_id: list(samples) for source_id, samples in self._samples.items()}
        return {
            "ok": True,
            "strategy": {
                "primary": "LSL/XDF for biosignal stream alignment",
                "tablet": "median ping-pong offset plus RTT from the participant browser",
                "remote_worker": "same offset/RTT contract when workers report it",
                "note": "Use these values for diagnostics and non-LSL metadata, not as a replacement for source timestamps.",
            },
            "sources": {
                source_id: _summarize_source(source_id, samples)
                for source_id, samples in samples_by_source.items()
                if samples
            },
        }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()

    def _append_sample(self, source_id: str, sample: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            samples = self._samples.setdefault(source_id, deque(maxlen=self.max_samples_per_source))
            samples.append(sample)
        return dict(sample)

    def _epoch_ms(self) -> int:
        return int(round(self._clock() * 1000))


def _summarize_source(source_id: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    latest = samples[-1]
    offsets = [sample["offset_ms"] for sample in samples if isinstance(sample.get("offset_ms"), (int, float))]
    rtts = [sample["rtt_ms"] for sample in samples if isinstance(sample.get("rtt_ms"), (int, float))]
    return {
        "source_id": source_id,
        "source_type": latest.get("source_type") or "unknown",
        "sample_count": len(samples),
        "last_seen_epoch_ms": latest.get("observed_at_epoch_ms"),
        "latest_offset_ms": latest.get("offset_ms"),
        "median_offset_ms": round(float(median(offsets)), 3) if offsets else None,
        "latest_rtt_ms": latest.get("rtt_ms"),
        "median_rtt_ms": round(float(median(rtts)), 3) if rtts else None,
        "last_server_processing_ms": latest.get("server_processing_ms"),
        "status": "synced" if offsets else "observing",
    }


def _normalize_source_id(value: str) -> str:
    return str(value or "").strip()[:128]


def _finite_float(value: Any) -> float | None:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(candidate):
        return None
    return candidate


def _rounded_optional_float(value: Any) -> float | None:
    candidate = _finite_float(value)
    return round(candidate, 3) if candidate is not None else None
