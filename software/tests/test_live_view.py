"""LiveView: the dashboard's 2 Hz / 60 s view of recorded samples (sensor data contract)."""
from __future__ import annotations

import math

from study_runner.contracts.sensor_contract import LIVE_VIEW_POINTS
from study_runner.plugin_framework.live_view import LiveView, empty_live, standardize_live

STREAMS = {"vitals": {"channels": ["heart", "breath", "seq"]}, "other": {"channels": ["x"]}}
SERIES = [{"key": "vitals", "stream": "vitals", "channels": ["heart", "breath"]}]


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_view(clock: Clock) -> LiveView:
    return LiveView(SERIES, STREAMS, clock=clock)


def channel(view: LiveView, name: str, now: float) -> list:
    return view.snapshot(now)["series"]["vitals"]["channels"][name]


def test_each_point_is_the_mean_of_half_a_second_and_the_newest_is_last() -> None:
    clock = Clock(1000.0)
    view = make_view(clock)
    view.observe("vitals", [60.0, 12.0, 1])
    clock.now = 1000.2
    view.observe("vitals", [62.0, float("nan"), 2])
    clock.now = 1000.6
    view.observe("vitals", [70.0, 14.0, 3])
    heart = channel(view, "heart", 1001.1)
    assert len(heart) == LIVE_VIEW_POINTS
    assert heart[-2:] == [61.0, 70.0]
    assert channel(view, "breath", 1001.1)[-2:] == [12.0, 14.0]  # NaN is left out of the mean
    assert all(value is None for value in heart[:-2])


def test_the_bucket_being_filled_is_not_shown_yet() -> None:
    clock = Clock(1000.1)
    view = make_view(clock)
    view.observe("vitals", [60.0, 12.0, 1])
    assert channel(view, "heart", 1000.3)[-1] is None
    assert channel(view, "heart", 1000.5)[-1] == 60.0


def test_points_older_than_sixty_seconds_drop_out() -> None:
    clock = Clock(1000.0)
    view = make_view(clock)
    view.observe("vitals", [60.0, 12.0, 1])
    assert channel(view, "heart", 1060.4)[0] == 60.0
    assert all(value is None for value in channel(view, "heart", 1060.6))


def test_a_bucket_with_only_missing_values_is_a_gap() -> None:
    clock = Clock(1000.0)
    view = make_view(clock)
    view.observe("vitals", [float("nan"), None, 1])
    assert channel(view, "heart", 1000.5)[-1] is None


def test_channels_are_matched_by_name_when_the_row_has_its_own_order() -> None:
    clock = Clock(1000.0)
    view = make_view(clock)
    view.observe("vitals", [5, 14.0, 66.0], channels=["seq", "breath", "heart"])
    assert channel(view, "heart", 1000.5)[-1] == 66.0
    assert channel(view, "breath", 1000.5)[-1] == 14.0


def test_validity_marks_a_whole_bucket_and_invalidate_breaks_the_current_one() -> None:
    clock = Clock(1000.0)
    view = make_view(clock)
    view.observe("vitals", [60.0, 12.0, 1], valid=True)
    view.observe("vitals", [61.0, 12.0, 2], valid=False)
    clock.now = 1000.5
    view.observe("vitals", [62.0, 12.0, 3], valid=True)
    clock.now = 1001.0
    view.observe("vitals", [63.0, 12.0, 4], valid=True)
    view.invalidate(["vitals"])
    valid = view.snapshot(1001.5)["series"]["vitals"]["valid"]
    assert valid[-3:] == [False, True, False]
    assert valid[0] is None


def test_series_without_validity_report_none() -> None:
    clock = Clock(1000.0)
    view = make_view(clock)
    view.observe("vitals", [60.0, 12.0, 1])
    assert view.snapshot(1000.5)["series"]["vitals"]["valid"] is None


def test_other_streams_and_reset() -> None:
    clock = Clock(1000.0)
    view = make_view(clock)
    assert not view.shows("other")
    view.observe("other", [1.0])
    view.observe("vitals", [60.0, 12.0, 1])
    view.reset()
    assert all(value is None for value in channel(view, "heart", 1000.5))


def test_standardize_live_forces_the_one_shape() -> None:
    raw = {
        "series": {
            "vitals": {"channels": {"heart": [1, float("nan"), float("inf"), "x", 72.5], "seq": [1, 2]},
                       "valid": [True, "no", False]},
            "undeclared": {"channels": {"heart": [1]}},
        }
    }
    live = standardize_live(raw, SERIES, running=True)
    vitals = live["series"]["vitals"]
    assert set(live["series"]) == {"vitals"}
    assert set(vitals["channels"]) == {"heart", "breath"}
    assert vitals["channels"]["heart"][-5:] == [1.0, None, None, None, 72.5]
    assert len(vitals["channels"]["heart"]) == LIVE_VIEW_POINTS
    assert vitals["channels"]["breath"] == [None] * LIVE_VIEW_POINTS
    assert vitals["valid"][-3:] == [True, None, False]
    assert (live["rate_hz"], live["window_s"], live["points"]) == (2, 60, LIVE_VIEW_POINTS)


def test_standardize_live_is_empty_when_not_running_or_missing() -> None:
    raw = {"series": {"vitals": {"channels": {"heart": [60.0]}}}}
    assert standardize_live(raw, SERIES, running=False) == empty_live(SERIES)
    assert standardize_live(None, SERIES, running=True) == empty_live(SERIES)
    long_list = list(range(500))
    trimmed = standardize_live({"series": {"vitals": {"channels": {"heart": long_list}}}}, SERIES, running=True)
    assert trimmed["series"]["vitals"]["channels"]["heart"] == [float(value) for value in long_list[-LIVE_VIEW_POINTS:]]
    assert not any(isinstance(value, float) and math.isnan(value) for value in trimmed["series"]["vitals"]["channels"]["heart"])
