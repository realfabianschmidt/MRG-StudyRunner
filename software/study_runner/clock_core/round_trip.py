"""Estimate another computer's clock from round trips (the NTP method).

One exchange: this computer notes ``sent`` and ``received`` around a request
that the other side answers with its own clock reading (``remote``). Then

    offset = remote - (sent + received) / 2      (their clock minus ours)
    rtt    = received - sent

and the true offset lies within +/- rtt / 2 of that, because the request and
the answer may travel for different times. So the exchange with the smallest
round trip is the most trustworthy one. The estimator keeps the recent
exchanges, trusts the one with the smallest round trip (aged by a drift
allowance, since two clocks drift apart), and recognises a step of the other
clock (for example NTP correcting the hub's clock): three exchanges that
agree with each other but not with the estimate replace the window.

Pure standard library, so the host and every plugin process can use it.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading


@dataclass(frozen=True)
class ClockExchange:
    sent: float
    received: float
    remote: float

    @property
    def rtt(self) -> float:
        return self.received - self.sent

    @property
    def offset(self) -> float:
        """The other clock minus ours, from this exchange alone."""
        return self.remote - (self.sent + self.received) / 2.0


@dataclass(frozen=True)
class OffsetEstimate:
    offset_s: float | None
    uncertainty_ms: float | None
    valid: bool
    exchanges: int
    steps: int

    def to_local(self, remote_time: float) -> float | None:
        """A reading of the other clock on our clock (None without an estimate)."""
        if self.offset_s is None or not isinstance(remote_time, (int, float)) or not math.isfinite(remote_time):
            return None
        return float(remote_time) - self.offset_s


class RoundTripOffsetEstimator:
    def __init__(
        self,
        *,
        window: int = 16,
        max_age_s: float = 60.0,
        max_rtt_s: float = 0.25,
        drift_ppm: float = 100.0,
        min_exchanges: int = 4,
        max_uncertainty_ms: float = 25.0,
        step_margin_s: float = 0.020,
        step_confirmations: int = 3,
    ) -> None:
        self.window = window
        self.max_age_s = max_age_s
        self.max_rtt_s = max_rtt_s
        self.drift = drift_ppm / 1_000_000.0
        self.min_exchanges = min_exchanges
        self.max_uncertainty_ms = max_uncertainty_ms
        self.step_margin_s = step_margin_s
        self.step_confirmations = step_confirmations
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._exchanges: deque[ClockExchange] = deque(maxlen=self.window)
            self._pending: list[ClockExchange] = []
            self._steps = 0
            self._rejected = 0

    @property
    def rejected(self) -> int:
        return self._rejected

    def add(self, exchange: ClockExchange) -> OffsetEstimate:
        """Take one exchange and return the estimate after it."""
        with self._lock:
            if not all(math.isfinite(value) for value in (exchange.sent, exchange.received, exchange.remote)):
                self._rejected += 1
            elif exchange.rtt < 0 or exchange.rtt > self.max_rtt_s:
                self._rejected += 1
            else:
                self._prune(exchange.received)
                best = self._best(exchange.received)
                if best is None or self._agrees(exchange, best, exchange.received):
                    self._exchanges.append(exchange)
                    self._pending = []
                else:
                    self._pending.append(exchange)
                    self._confirm_step()
            return self._estimate(exchange.received)

    def estimate(self, now: float | None = None) -> OffsetEstimate:
        """The current estimate, aged to ``now`` (our clock)."""
        with self._lock:
            if now is None:
                now = self._exchanges[-1].received if self._exchanges else 0.0
            return self._estimate(now)

    # ------------------------------------------------------------- helpers

    def _prune(self, now: float) -> None:
        while self._exchanges and now - self._exchanges[0].received > self.max_age_s:
            self._exchanges.popleft()

    def _error(self, exchange: ClockExchange, now: float) -> float:
        """Worst-case error of an exchange's offset at ``now``, in seconds."""
        return exchange.rtt / 2.0 + max(0.0, now - exchange.received) * self.drift

    def _best(self, now: float) -> ClockExchange | None:
        recent = [item for item in self._exchanges if now - item.received <= self.max_age_s]
        return min(recent, key=lambda item: self._error(item, now)) if recent else None

    def _agrees(self, exchange: ClockExchange, best: ClockExchange, now: float) -> bool:
        tolerance = (exchange.rtt + best.rtt) / 2.0 + max(0.0, now - best.received) * self.drift + self.step_margin_s
        return abs(exchange.offset - best.offset) <= tolerance

    def _confirm_step(self) -> None:
        pending = self._pending[-self.step_confirmations:]
        if len(pending) < self.step_confirmations:
            return
        offsets = [item.offset for item in pending]
        spread = max(item.rtt for item in pending) + self.step_margin_s
        if max(offsets) - min(offsets) <= spread:
            # The other clock stepped: start again from the exchanges that agree.
            self._exchanges = deque(pending, maxlen=self.window)
            self._pending = []
            self._steps += 1
        else:
            self._pending = pending[1:]

    def _estimate(self, now: float) -> OffsetEstimate:
        best = self._best(now)
        if best is None:
            return OffsetEstimate(None, None, False, 0, self._steps)
        count = sum(1 for item in self._exchanges if now - item.received <= self.max_age_s)
        uncertainty_ms = self._error(best, now) * 1000.0
        # While exchanges contradict the estimate (a possible step), trust nothing.
        valid = count >= self.min_exchanges and uncertainty_ms <= self.max_uncertainty_ms and not self._pending
        return OffsetEstimate(best.offset, round(uncertainty_ms, 3), valid, count, self._steps)
