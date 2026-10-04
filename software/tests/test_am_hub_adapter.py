"""AM Hub acquisition on the sensor data contract: one sample per real board
frame in its own numeric stream, every other hub event verbatim in
``hub_events``, the hub clock from the pings in ``hub_clock``, and the
measured latency per frame (a reversible correction when switched on)."""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fake_lsl import FakePylsl, FakeStreamOutlet  # noqa: E402

from study_runner.plugins.sensors.am_hub import adapter
from study_runner.data_core.worker.lsl_recording import LslSourceRecorder, ProjectionCache
from study_runner.data_core.worker.core import NativeXdfCore
from study_runner.plugin_framework.registry import get_plugin_manifest

# The board topics of MRG-ParasiteV2/am_hub/amhub/plugins/sensors/*/codec.py.
# The recorded channels must follow the hub exactly; a firmware change shows up here.
HUB_TOPICS = {
    "radar": (
        "/sensor/personDist", "/sensor/personX", "/sensor/personY", "/sensor/targetCount",
        "/sensor/t1x", "/sensor/t1y", "/sensor/t1speed", "/sensor/t1res",
        "/sensor/t2x", "/sensor/t2y", "/sensor/t2speed", "/sensor/t2res",
        "/sensor/t3x", "/sensor/t3y", "/sensor/t3speed", "/sensor/t3res",
        "/sensor/presence", "/sensor/presState", "/sensor/presDist",
        "/sensor/presMoveEnergy", "/sensor/presStaticEnergy", "/sensor/presDetDist",
    ),
    "bio": ("/sensor/heartBpm", "/sensor/breathRate", "/sensor/bioDist", "/sensor/bioT1x", "/sensor/bioT1y"),
    "valves": tuple(f"/solenoid/CH{index}" for index in range(8)),
}


HUB_OFFSET = 1_700_000_000.0  # the hub's wall clock minus this computer's LSL clock
LSL = FakePylsl(clock=1000.0)


@pytest.fixture(autouse=True)
def reset_adapter():
    global LSL
    LSL = FakePylsl(clock=1000.0)
    adapter._streams.use_backend(LSL)
    adapter._streams.reset()
    adapter._running = False
    adapter._generation += 1
    adapter._config = {"enabled": True, "base_url": "http://hub", "data_timeout_seconds": 5.0}
    adapter._monitor.reset()
    adapter._history.clear()
    adapter._hub_rtts.clear()
    adapter._clock.reset()
    adapter._correction_active = False
    for latencies in adapter._latencies.values():
        latencies.clear()
    adapter._api_unsupported = False
    adapter._reset_link()
    adapter._latest_state = {"status": "configured", "last_message": ""}
    yield
    adapter._running = False
    adapter._generation += 1


def frame(role: str, seq: int, values: dict[str, object], t: float = 1.25) -> str:
    return json.dumps({"type": "frame", "role": role, "device": f"{role}_hub",
                       "seq": seq, "t": t, "values": values}, separators=(",", ":"))


def active() -> dict[str, FakeStreamOutlet]:
    assert adapter._streams.open_all()
    outlets = {key: LSL.outlet(contract["source_id"]) for key, contract in adapter.STREAM_CONTRACTS.items()}
    adapter._running = True
    adapter._link_opened()
    return outlets


def known_hub_clock(link_rtt_ms: float = 10.0, role: str = "radar") -> None:
    """Four symmetric pings (2 ms each way) and the hub's radio round trip for ``role``."""
    for index in range(4):
        sent = 990.0 + index
        adapter._record_ping(sent, sent + 0.004, sent + 0.002 + HUB_OFFSET)
    adapter._handle_sse_event(json.dumps({"type": "status", "devices": {
        f"{role}_hub": {"role": role, "connected": True, "link_rtt_ms": link_rtt_ms}}}))


def row(stream: str, sample: list) -> dict[str, float]:
    return dict(zip(adapter.STREAM_CONTRACTS[stream]["channels"], sample))


# ------------------------------------------------------------ data model

def test_recorded_channels_are_the_hub_codec_topics_plus_bookkeeping():
    bookkeeping = ["rssi", "seq", "hub_timestamp", "latency_ms", "correction_ms"]
    for stream, topics in HUB_TOPICS.items():
        contract = adapter.STREAM_CONTRACTS[stream]
        assert contract["channels"] == [address.rsplit("/", 1)[-1] for address in topics] + bookkeeping
        assert contract["sequence_channel"] == "seq"
        assert contract["timing"]["timestamp_source"] == "host_arrival_corrected"
        assert contract["timing"]["correction_channel"] == "correction_ms"
    assert adapter.STREAM_CONTRACTS["hub_events"]["channel_format"] == "string"
    assert adapter.STREAM_CONTRACTS["hub_clock"]["timing"]["timestamp_source"] == "host_arrival"


def test_a_board_frame_is_exactly_one_sample_in_its_stream_with_its_own_values():
    outlets = active()
    adapter._handle_sse_event(frame("radar", 7, {"/sensor/presMoveEnergy": 0, "/sensor/t1speed": -12,
                                                "/sensor/rssiRadar": -61}, t=1700000000.125))
    adapter._handle_sse_event(frame("bio", 3, {"/sensor/heartBpm": 72, "/sensor/bioDist": 85}))
    adapter._handle_sse_event(frame("solenoid", 1, {"/solenoid/CH3": 1}))
    radar, bio, valves = (outlets[key].rows for key in ("radar", "bio", "valves"))
    assert len(radar) == len(bio) == len(valves) == 1
    radar_row = row("radar", radar[0])
    # As the ESP sent them: no unit conversion, 0 stays 0 (see README).
    assert radar_row["presMoveEnergy"] == 0 and radar_row["t1speed"] == -12
    assert radar_row["rssi"] == -61 and radar_row["seq"] == 7
    assert radar_row["hub_timestamp"] == 1700000000.125
    assert math.isnan(radar_row["personDist"])  # not in this frame: missing, never 0
    # No hub clock known yet: no latency, and the timestamp is the arrival.
    assert math.isnan(radar_row["latency_ms"]) and radar_row["correction_ms"] == 0
    assert outlets["radar"].timestamps == [1000.0]
    assert row("bio", bio[0])["bioDist"] == 85
    assert row("valves", valves[0])["CH3"] == 1 and math.isnan(row("valves", valves[0])["CH0"])
    assert outlets["hub_events"].rows == []


def test_other_hub_events_are_kept_verbatim_in_hub_events():
    outlets = active()
    events = [
        '{"type":"hello","host":{"wifi_power_save":"off"},"future":true}',
        '{"type":"status","devices":{}}',
        '{"type":"gap","dropped":2}',
        '{"type":"unknown","new_field":[1,2,3]}',
        "not json at all",
    ]
    for event in events:
        adapter._handle_sse_event(event)
    assert outlets["hub_events"].rows == [[event] for event in events]
    assert all(outlets[key].rows == [] for key in ("radar", "bio", "valves"))
    assert adapter.get_status()["data_quality"]["hub_dropped_events"] == 2


def test_a_frame_with_a_value_its_stream_does_not_declare_is_also_kept_whole():
    outlets = active()
    event = frame("solenoid", 1, {"/solenoid/CH0": 1, "/solenoid/alive": 1})
    adapter._handle_sse_event(event)
    assert len(outlets["valves"].rows) == 1
    assert outlets["hub_events"].rows == [[event]]
    assert "/solenoid/alive" in adapter.get_status()["unknown_topics"]


def test_no_frame_means_no_sample_and_no_fixed_clock():
    outlets = active()
    assert all(outlet.rows == [] for outlet in outlets.values())
    assert not hasattr(adapter, "_publish_loop")


def test_a_failed_push_is_counted_and_acquisition_goes_on():
    outlets = active()
    outlets["radar"].fail = OSError("LSL unavailable")
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presMoveEnergy": 2}))
    adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 70}))
    health = adapter._streams.status_blocks()["stream_health"]
    assert health["failed"] is True and "LSL unavailable" in health["last_error"]
    assert len(outlets["bio"].rows) == 1  # the other boards are still recorded


def test_the_backup_projection_reads_recorded_channels():
    manifest = get_plugin_manifest("am_hub")
    projection = manifest["capability_config"]["backup_projection"]
    for channel in projection["channels"]:
        assert channel["channel"] in adapter.STREAM_CONTRACTS[channel["stream"]]["channels"], channel


# --------------------------------------------------------- readiness / view

def test_readiness_needs_fresh_radar_and_bio_frames_not_a_person():
    active()
    adapter._handle_sse_event('{"type":"status","devices":{}}')
    assert adapter.get_status()["connection"]["phase"] != "connected"
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presence": 0}))
    assert adapter.get_status()["status"] == "waiting"
    adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 0}))
    status = adapter.get_status()
    assert status["status"] == "no_presence"
    assert status["connection"]["phase"] == "connected"


def test_a_silent_board_blocks_readiness_while_the_hub_link_stays_open():
    active()
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presence": 0}))
    adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 0}))
    adapter._monitor.frame_times["bio"][-1] = time.time() - 6
    adapter._handle_sse_event('{"type":"status","devices":{}}')
    status = adapter.get_status()
    assert status["link"]["state"] == "open"
    assert status["boards"]["bio"]["live"] is False
    assert status["status"] == "waiting"


def test_a_reported_board_disconnect_blocks_readiness_at_once():
    active()
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presence": 0}))
    adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 0}))
    adapter._handle_sse_event('{"type":"status","devices":{"bio_hub":{"role":"bio","connected":false}}}')
    status = adapter.get_status()
    assert status["hub_boards"]["bio"]["connected"] is False
    assert status["status"] == "waiting"


def test_the_view_uses_the_hub_names_and_only_real_frames():
    clock = [100.1]
    adapter._streams._live._clock = lambda: clock[0]
    try:
        active()
        adapter._handle_sse_event(frame("radar", 1, {"/sensor/presMoveEnergy": 0, "/sensor/t1speed": 2}))
        adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 0, "/sensor/breathRate": 12}))
        adapter._handle_sse_event('{"type":"status","devices":{}}')
        status = adapter.get_status()
        assert status["latest"]["presMoveEnergy"] == 0 and status["latest"]["heartBpm"] == 0
        assert "preview" not in status
        clock[0] = 100.6
        live = adapter._streams.status_blocks()["live"]["series"]
        # The live view shows the recorded frames, nothing else.
        assert live["movement"]["channels"]["presMoveEnergy"][-1] == 0
        assert live["vitals"]["channels"]["breathRate"][-1] == 12
        assert live["position"]["channels"]["personX"][-1] is None
    finally:
        adapter._streams._live._clock = time.monotonic


def test_sequence_gaps_and_hub_drops_are_counted_separately():
    active()
    adapter._handle_sse_event(frame("radar", 1, {}))
    adapter._handle_sse_event(frame("radar", 4, {}))
    adapter._handle_sse_event('{"type":"gap","dropped":3}')
    quality = adapter.get_status()["data_quality"]
    assert quality["seq_gaps"]["radar"] == 2
    assert quality["hub_dropped_events"] == 3


def test_stop_and_start_forget_the_view_but_not_what_was_recorded():
    outlets = active()
    known_hub_clock()
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presMoveEnergy": 17}))
    adapter.stop()
    status = adapter.get_status()
    assert status["latest"] == {} and status["timing"] == {} and status["seconds_since_last_activity"] is None
    with mock.patch.object(adapter, "_sse_loop"), mock.patch.object(adapter, "_ping_loop"):
        adapter.start()
    try:
        status = adapter.get_status()
        assert status["latest"] == {}
        assert status["timing"]["clock_offset_valid"] is False  # a new run measures the hub clock anew
        assert status["timing"]["latency_ms"] == {"radar": None, "bio": None, "valves": None}
        assert adapter._history == type(adapter._history)(maxlen=adapter._history.maxlen)
        assert len(outlets["radar"].rows) == 1
    finally:
        adapter.stop()


def test_disabling_the_configuration_stops_a_running_adapter():
    active()
    adapter.initialize(enabled=False, base_url="http://hub")
    status = adapter.get_status()
    assert status["running"] is False and status["status"] == "disabled"


def test_card_summaries_average_the_recorded_frames():
    active()
    with mock.patch.object(adapter.time, "time", return_value=100.0):
        adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 70, "/sensor/breathRate": 12}))
    with mock.patch.object(adapter.time, "time", return_value=101.0):
        adapter._handle_sse_event(frame("bio", 2, {"/sensor/heartBpm": 74}))
        adapter._handle_sse_event(frame("radar", 1, {"/sensor/presence": 1, "/sensor/presMoveEnergy": 40}))
    summary = adapter.get_interval_summary(99.0, 102.0)
    assert summary["avg_heart_rate"] == 72 and summary["avg_breath_rate"] == 12
    assert summary["avg_presence"] == 1 and summary["frame_counts"] == {"radar": 1, "bio": 2, "valves": 0}
    assert len(adapter.export_interval_samples(99.0, 102.0)) == 3


def test_card_averages_skip_the_esp_zero_for_no_value_but_count_it():
    outlets = active()
    with mock.patch.object(adapter.time, "time", return_value=100.0):
        for seq, bpm in enumerate((72, 0, 0, 74), start=1):
            adapter._handle_sse_event(frame("bio", seq, {"/sensor/heartBpm": bpm}))
    summary = adapter.get_interval_summary(99.0, 101.0)
    assert summary["avg_heart_rate"] == 73 and summary["zero_frames"]["heartBpm"] == 2
    assert [row("bio", r)["heartBpm"] for r in outlets["bio"].rows] == [72, 0, 0, 74]  # XDF keeps the zeros


# ------------------------------------------------------- timing: ping and latency

def test_each_answered_ping_is_one_hub_clock_sample():
    outlets = active()
    known_hub_clock()
    rows = [row("hub_clock", r) for r in outlets["hub_clock"].rows]
    assert len(rows) == 4
    assert rows[0]["hub_clock_s"] == pytest.approx(990.002 + HUB_OFFSET)
    assert rows[0]["rtt_ms"] == pytest.approx(4.0)
    assert rows[0]["exchange_offset_s"] == pytest.approx(HUB_OFFSET)
    assert [r["offset_valid"] for r in rows] == [0.0, 0.0, 0.0, 1.0]  # four exchanges needed
    assert rows[-1]["clock_offset_s"] == pytest.approx(HUB_OFFSET)
    assert rows[-1]["correction_enabled"] == 0.0
    assert outlets["hub_clock"].timestamps == pytest.approx([990.004, 991.004, 992.004, 993.004])


def test_a_ping_without_the_hub_clock_still_records_the_round_trip():
    outlets = active()
    adapter._record_ping(10.0, 10.006, None)
    recorded = row("hub_clock", outlets["hub_clock"].rows[0])
    assert recorded["rtt_ms"] == pytest.approx(6.0)
    assert math.isnan(recorded["hub_clock_s"]) and recorded["offset_valid"] == 0.0


def test_the_latency_is_hub_transit_plus_half_the_radio_round_trip():
    outlets = active()
    known_hub_clock(link_rtt_ms=10.0)
    LSL.clock = 1000.0
    # The hub stamped the packet 40 ms before it arrived here (on our clock).
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presence": 1}, t=999.960 + HUB_OFFSET))
    recorded = row("radar", outlets["radar"].rows[0])
    assert recorded["latency_ms"] == pytest.approx(45.0, abs=0.01)  # 40 ms hub->here + 10 ms / 2 radio
    # Measuring only (the default): the timestamp is the arrival time.
    assert outlets["radar"].timestamps == [1000.0] and recorded["correction_ms"] == 0.0
    assert adapter.get_status()["timing"]["latency_ms"]["radar"] == pytest.approx(45.0, abs=0.1)
    assert adapter.get_status()["hub_boards"]["radar"]["latency_ms"] == pytest.approx(45.0, abs=0.1)


def test_with_the_correction_on_the_timestamp_is_reversible():
    outlets = active()
    known_hub_clock(link_rtt_ms=10.0)
    adapter._correction_active = True
    LSL.clock = 1000.0
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presence": 1}, t=999.960 + HUB_OFFSET))
    (values, stamp), = outlets["radar"].samples
    recorded = row("radar", values)
    assert stamp == pytest.approx(1000.0 - 0.045, abs=1e-6)
    assert recorded["correction_ms"] == pytest.approx(recorded["latency_ms"])
    assert stamp + recorded["correction_ms"] / 1000.0 == pytest.approx(1000.0)  # the arrival time


def test_an_implausible_or_unknown_latency_is_recorded_but_never_applied():
    outlets = active()
    known_hub_clock(link_rtt_ms=10.0)
    adapter._correction_active = True
    LSL.clock = 1000.0
    # 5 s: no plausible latency for a live sensor (e.g. a hub clock not yet stepped).
    adapter._handle_sse_event(frame("radar", 1, {}, t=995.0 + HUB_OFFSET))
    # No radio round trip from the bio board (older firmware): no latency at all.
    adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 70}, t=999.990 + HUB_OFFSET))
    radar_row = row("radar", outlets["radar"].rows[0])
    bio_row = row("bio", outlets["bio"].rows[0])
    assert radar_row["latency_ms"] == pytest.approx(5005.0, abs=0.1)
    assert outlets["radar"].timestamps == [1000.0] and radar_row["correction_ms"] == 0.0
    assert math.isnan(bio_row["latency_ms"]) and outlets["bio"].timestamps == [1000.0]


def test_corrected_timestamps_never_go_backwards():
    outlets = active()
    known_hub_clock(link_rtt_ms=10.0)
    adapter._correction_active = True
    LSL.clock = 1000.0
    adapter._handle_sse_event(frame("radar", 1, {}, t=999.990 + HUB_OFFSET))  # 15 ms
    LSL.clock = 1000.005
    adapter._handle_sse_event(frame("radar", 2, {}, t=999.900 + HUB_OFFSET))  # a late packet: 110 ms
    first, second = outlets["radar"].timestamps
    assert second >= first
    for values, stamp in outlets["radar"].samples:
        arrival = stamp + row("radar", values)["correction_ms"] / 1000.0
        assert arrival in (pytest.approx(1000.0), pytest.approx(1000.005))


def test_the_correction_setting_is_frozen_for_a_run():
    active()
    adapter._config["timestamp_correction"] = True
    with mock.patch.object(adapter, "_sse_loop"), mock.patch.object(adapter, "_ping_loop"):
        adapter.stop()
        adapter.start()
    try:
        assert adapter._correction_active is True
        adapter._config["timestamp_correction"] = False  # saved, not yet restarted
        timing = adapter.get_status()["timing"]
        assert timing["correction_enabled"] is True and timing["correction_configured"] is False
    finally:
        adapter.stop()


def test_the_ping_answer_must_carry_a_number():
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    assert adapter._ping_server_now(Response({"ok": True, "server_now": 1700000000.5})) == 1700000000.5
    assert adapter._ping_server_now(Response({"ok": True})) is None
    assert adapter._ping_server_now(Response({"server_now": "soon"})) is None
    assert adapter._ping_server_now(Response({"server_now": True})) is None


# --------------------------------------------------------------- connection

def test_a_hub_without_v2_is_reported_without_trying_anything_else():
    requested: list[str] = []

    class Response:
        status_code = 404

        def close(self):
            pass

    def get(_session, url, **_kwargs):
        requested.append(url)
        return Response()

    adapter._running = True
    generation = adapter._generation
    with mock.patch("requests.Session.get", autospec=True, side_effect=get):
        adapter._sse_loop(generation)
    assert requested == ["http://hub/api/v2/stream"]
    adapter._running = True
    status = adapter.get_status()
    assert status["api_unsupported"] is True and status["status"] == "failed"


def test_a_stimulus_command_posts_the_cards_identity():
    captured = {}

    class Response:
        ok = True
        status_code = 200

    def post(url, json=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return Response()

    with mock.patch("requests.post", side_effect=post):
        result = adapter.send_stimulus_command(
            "start",
            {"event_id": "e1", "stimulus_id": "s1", "study_id": "study", "session_id": "sess",
             "question_index": 3, "source_epoch_ms": 123.0, "plugin_actions": {"ignored": True}},
        )
    assert captured["url"] == "http://hub/api/v2/stimulus/start"
    assert captured["json"] == {
        "event_id": "e1", "stimulus_id": "s1", "study_id": "study", "session_id": "sess",
        "question_index": 3, "source_epoch_ms": 123.0,
    }
    assert result["ok"] is True and result["outcome"] == "accepted"
    assert adapter.get_status()["last_stimulus_command"]["outcome"] == "accepted"


def test_a_hub_without_the_stimulus_endpoint_never_blocks_the_trial():
    class Response:
        ok = False
        status_code = 404

    with mock.patch("requests.post", return_value=Response()):
        result = adapter.send_stimulus_command("stop", {"event_id": "e1", "stimulus_id": "s1"})
    assert result["ok"] is False
    assert result["outcome"] == "unsupported"


def test_an_unreachable_hub_is_reported_not_raised():
    with mock.patch("requests.post", side_effect=OSError("refused")):
        result = adapter.send_stimulus_command("start", {"event_id": "e1", "stimulus_id": "s1"})
    assert result["ok"] is False
    assert result["outcome"] == "unreachable"


def test_an_unconfigured_hub_never_attempts_the_request():
    adapter._config = {"enabled": False, "base_url": ""}
    with mock.patch("requests.post") as post:
        result = adapter.send_stimulus_command("start", {"event_id": "e1", "stimulus_id": "s1"})
    post.assert_not_called()
    assert result["outcome"] == "not_configured"


def test_sse_byte_chunks_arrive_as_whole_events():
    outlets = active()
    event = frame("bio", 2, {"/sensor/heartBpm": 70})
    body = f"data: {event}\r\n\r\n: keepalive\r\n\r\ndata: {{\"type\":\"gap\",\"dropped\":1}}\r\n\r\n".encode()

    class Response:
        raw = None

        def iter_content(self, chunk_size=None):
            return iter(body[index:index + 3] for index in range(0, len(body), 3))

    assert adapter._read_sse_events(Response(), adapter._generation)
    assert row("bio", outlets["bio"].rows[0])["heartBpm"] == 70
    assert outlets["hub_events"].rows == [['{"type":"gap","dropped":1}']]


# ------------------------------------------------------------ XDF boundary

def _replay(outlets: dict[str, FakeStreamOutlet]) -> None:
    for event in (
        frame("radar", 1, {"/sensor/presMoveEnergy": 0, "/sensor/presence": 1}),
        '{"type":"gap","dropped":1}',
        frame("radar", 2, {"/sensor/presMoveEnergy": 5, "/sensor/presence": 1}),
    ):
        adapter._handle_sse_event(event)


def test_replayed_frames_reach_the_xdf_writer_once_in_order(tmp_path):
    outlets = active()
    _replay(outlets)

    class Writer:
        def __init__(self):
            self.rows = []

        def write_samples(self, stream_id, timestamps, rows, *, channel_format, channel_count):
            assert channel_format == "double64" and channel_count == 27
            self.rows.extend(zip(timestamps, rows))

        def write_clock_offset(self, *_args):
            pass

    writer = Writer()
    recorder = LslSourceRecorder(
        mock.Mock(create_writer=lambda _path: writer), plugin_key="am_hub",
        target_path=Path(tmp_path) / "am.xdf", streams=get_plugin_manifest("am_hub")["streams"],
        cache=ProjectionCache(), pylsl_module=mock.Mock(local_clock=lambda: 200.0),
    )
    radar_state = next(state for state in recorder._states if state.spec.key == "radar")

    class Inlet:
        def pull_chunk(self, **_kwargs):
            recorder._stop.set()
            return outlets["radar"].rows, [100.0, 100.2]

        def time_correction(self, **_kwargs):
            return 0.0

        def close_stream(self):
            pass

    with mock.patch.object(recorder, "_connect", return_value=Inlet()):
        recorder._record_stream(radar_state)
    assert [row("radar", r)["presMoveEnergy"] for _t, r in writer.rows] == [0, 5]
    assert [t for t, _r in writer.rows] == [100.0, 100.2]


def test_native_xdf_readback_keeps_the_frame_values_when_core_is_available(tmp_path):
    core_path = os.environ.get("STUDY_RUNNER_XDF_CORE_TEST")
    if not core_path:
        pytest.skip("native XDF core path not provided")
    import pyxdf

    outlets = active()
    _replay(outlets)
    stream = adapter.STREAM_CONTRACTS["radar"]
    path = Path(tmp_path) / "am-radar.xdf"
    writer = NativeXdfCore(Path(core_path)).create_writer(path)
    channels = "".join(f"<channel><label>{c}</label></channel>" for c in stream["channels"])
    header = (
        '<?xml version="1.0"?><info><name>AmHub_RADAR</name><type>RADAR</type>'
        f'<channel_count>{len(stream["channels"])}</channel_count><nominal_srate>10</nominal_srate>'
        '<channel_format>double64</channel_format><source_id>study_runner.am_hub.radar</source_id>'
        '<version>1.100000</version><created_at>1</created_at><uid>am-replay</uid>'
        f'<session_id>smoke</session_id><hostname>localhost</hostname><desc><channels>{channels}</channels></desc></info>'
    )
    footer = (
        '<?xml version="1.0"?><info><first_timestamp>1000</first_timestamp>'
        '<last_timestamp>1000.1</last_timestamp><sample_count>2</sample_count><clock_offsets/></info>'
    )
    try:
        writer.write_stream_header(1, header)
        writer.write_samples(1, [1000.0, 1000.1], outlets["radar"].rows,
                             channel_format="double64", channel_count=len(stream["channels"]))
        writer.write_stream_footer(1, footer)
        writer.close(durable=True)
    finally:
        writer.destroy()
    streams, _header = pyxdf.load_xdf(str(path), synchronize_clocks=False, handle_clock_resets=False,
                                      dejitter_timestamps=False, verbose=False)
    move = stream["channels"].index("presMoveEnergy")
    assert [values[move] for values in streams[0]["time_series"]] == [0, 5]
