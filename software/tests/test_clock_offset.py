"""RoundTripOffsetEstimator: another computer's clock from ping round trips."""
from __future__ import annotations

import pytest

from study_runner.clock_core.round_trip import ClockExchange, RoundTripOffsetEstimator

OFFSET = 1_700_000_000.0  # the hub's wall clock minus this computer's LSL clock


def exchange(sent: float, out_s: float, back_s: float, *, offset: float = OFFSET) -> ClockExchange:
    """The hub reads its clock after the request travelled ``out_s``."""
    return ClockExchange(sent=sent, received=sent + out_s + back_s, remote=sent + out_s + offset)


def feed(estimator: RoundTripOffsetEstimator, start: float, count: int, out_s: float, back_s: float, **kwargs):
    estimate = None
    for index in range(count):
        estimate = estimator.add(exchange(start + index, out_s, back_s, **kwargs))
    return estimate


def test_symmetric_delays_give_the_exact_offset() -> None:
    estimate = feed(RoundTripOffsetEstimator(), 100.0, 4, 0.002, 0.002)
    assert estimate.valid
    assert estimate.offset_s == pytest.approx(OFFSET, abs=1e-9)
    assert estimate.to_local(OFFSET + 105.0) == pytest.approx(105.0)


def test_asymmetric_delays_stay_within_half_the_round_trip() -> None:
    estimate = feed(RoundTripOffsetEstimator(), 100.0, 4, 0.009, 0.001)
    assert abs(estimate.offset_s - OFFSET) <= 0.005 + 1e-9
    assert estimate.uncertainty_ms >= 5.0


def test_the_fastest_round_trip_wins_and_slow_ones_are_rejected() -> None:
    estimator = RoundTripOffsetEstimator()
    estimator.add(exchange(100.0, 0.040, 0.002))
    estimator.add(exchange(101.0, 0.001, 0.001))
    estimator.add(exchange(102.0, 0.030, 0.010))
    estimator.add(exchange(103.0, 0.200, 0.200))  # 400 ms: rejected
    estimate = estimator.add(exchange(104.0, 0.020, 0.002))
    assert estimator.rejected == 1
    assert estimate.exchanges == 4
    assert estimate.offset_s == pytest.approx(OFFSET, abs=1e-6)


def test_too_few_or_too_uncertain_exchanges_are_not_valid() -> None:
    estimator = RoundTripOffsetEstimator()
    assert not feed(estimator, 100.0, 3, 0.002, 0.002).valid
    assert not feed(RoundTripOffsetEstimator(), 100.0, 6, 0.030, 0.030).valid  # +/- 30 ms
    assert estimator.estimate().offset_s is not None
    assert RoundTripOffsetEstimator().estimate().offset_s is None


def test_a_single_contradicting_exchange_suspends_trust_but_moves_nothing() -> None:
    estimator = RoundTripOffsetEstimator()
    feed(estimator, 100.0, 6, 0.002, 0.002)
    estimate = estimator.add(exchange(106.0, 0.002, 0.002, offset=OFFSET + 0.5))
    assert not estimate.valid and estimate.offset_s == pytest.approx(OFFSET, abs=1e-6)
    estimate = estimator.add(exchange(107.0, 0.002, 0.002))
    assert estimate.valid and estimate.steps == 0


def test_a_step_of_the_other_clock_is_taken_after_three_agreeing_exchanges() -> None:
    estimator = RoundTripOffsetEstimator()
    feed(estimator, 100.0, 6, 0.002, 0.002)
    estimate = feed(estimator, 106.0, 3, 0.002, 0.002, offset=OFFSET + 0.5)
    assert estimate.steps == 1
    assert estimate.offset_s == pytest.approx(OFFSET + 0.5, abs=1e-6)
    assert not estimate.valid  # only three exchanges since the step
    estimate = feed(estimator, 109.0, 1, 0.002, 0.002, offset=OFFSET + 0.5)
    assert estimate.valid


def test_drift_is_followed_as_old_exchanges_age_out() -> None:
    estimator = RoundTripOffsetEstimator()
    drift = 50e-6  # the other clock gains 50 us per second
    estimate = None
    for second in range(0, 300):
        estimate = estimator.add(exchange(100.0 + second, 0.002, 0.002, offset=OFFSET + drift * second))
    assert estimate.valid
    assert estimate.offset_s == pytest.approx(OFFSET + drift * 299, abs=0.004)


def test_reset_forgets_everything() -> None:
    estimator = RoundTripOffsetEstimator()
    feed(estimator, 100.0, 6, 0.002, 0.002)
    estimator.reset()
    assert estimator.estimate().offset_s is None and estimator.estimate().steps == 0
