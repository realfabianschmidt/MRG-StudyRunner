"""AM Hub dashboard view (monitor.py): bounded, derived only from real events."""
from __future__ import annotations

from study_runner.plugins.sensors.am_hub.monitor import AmHubMonitor, channel_name


def frame(role, seq, values):
    return {"type": "frame", "role": role, "device": f"{role}_hub", "seq": seq, "t": 1.0, "values": values}


def status(role, **info):
    return {"type": "status", "devices": {f"{role}_hub": {"role": role, **info}}}


def test_channel_names_are_the_hub_names_without_their_path():
    assert channel_name("/sensor/heartBpm") == "heartBpm"
    assert channel_name("/solenoid/CH3") == "CH3"


def test_reset_forgets_every_value_graph_and_counter():
    monitor = AmHubMonitor()
    monitor.observe(frame("radar", 1, {"/sensor/presMoveEnergy": 5}), 10.0)
    monitor.observe({"type": "gap", "dropped": 2}, 10.0)
    monitor.reset()
    view = monitor.snapshot(10.0, fresh_seconds=5, hub_rtt_ms=None)
    assert view["latest"] == {} and view["topics"] == {} and "preview" not in view
    assert view["data_quality"] == {"frames": {}, "seq_gaps": {}, "hub_dropped_events": 0}


def test_lost_packets_count_from_this_connection_and_survive_a_hub_restart():
    monitor = AmHubMonitor()
    for count in (40, 43):
        monitor.observe(status("radar", connected=True, gap_count=count), 1.0)
    assert monitor.snapshot(1.0, fresh_seconds=5, hub_rtt_ms=None)["hub_boards"]["radar"]["lost"] == 3
    monitor.observe(status("radar", connected=True, gap_count=2), 2.0)
    assert monitor.snapshot(2.0, fresh_seconds=5, hub_rtt_ms=None)["hub_boards"]["radar"]["lost"] == 2


def test_the_radio_round_trip_is_known_only_for_a_connected_board():
    # The latency itself is measured per frame by the adapter.
    monitor = AmHubMonitor()
    assert monitor.link_rtt_ms("bio") is None
    monitor.observe(status("bio", connected=True, link_rtt_ms=20.0, hub_latency_ms=1.5), 1.0)
    assert monitor.link_rtt_ms("bio") == 20.0
    monitor.observe(status("bio", connected=False, link_rtt_ms=20.0), 2.0)
    assert monitor.link_rtt_ms("bio") is None
    monitor.observe(status("radar", connected=True), 2.0)  # older firmware: no ping
    assert monitor.link_rtt_ms("radar") is None


def test_a_stale_board_status_no_longer_answers_for_the_radio_round_trip():
    """A status reply older than STATUS_STALE_SECONDS must not stand in for
    the board's *current* radio RTT - the caller passes `now` to find out."""
    monitor = AmHubMonitor()
    monitor.observe(status("bio", connected=True, link_rtt_ms=20.0), 1.0)
    assert monitor.link_rtt_ms("bio", 1.5) == 20.0  # 0.5 s old: still fresh
    assert monitor.link_rtt_ms("bio", 3.5) is None  # 2.5 s old: too stale
    assert monitor.link_rtt_ms("bio") == 20.0  # no `now` given: no staleness check (back-compat)


def test_a_person_is_seen_by_any_fresh_source_and_old_values_do_not_count():
    monitor = AmHubMonitor()
    monitor.observe(frame("bio", 1, {"/sensor/heartBpm": 70}), 10.0)
    assert monitor.snapshot(11.0, fresh_seconds=5, hub_rtt_ms=None)["person"] == {"detected": True, "sources": ["vitals"]}
    assert monitor.snapshot(20.0, fresh_seconds=5, hub_rtt_ms=None)["person"]["detected"] is False
