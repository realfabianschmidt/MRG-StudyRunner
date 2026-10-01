"""AM Hub acquisition like BrainBit: one sample per real board frame in its own
numeric stream, every other hub event verbatim in ``hub_events``."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from unittest import mock

import pytest

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


class Outlet:
    def __init__(self, fail: bool = False) -> None:
        self.samples: list[list] = []
        self.fail = fail

    def push_sample(self, values: list) -> None:
        if self.fail:
            raise OSError("LSL unavailable")
        self.samples.append(list(values))


@pytest.fixture(autouse=True)
def reset_adapter():
    adapter._running = False
    adapter._generation += 1
    adapter._config = {"enabled": True, "base_url": "http://hub", "data_timeout_seconds": 5.0}
    adapter._lsl_outlets = {}
    adapter._monitor.reset()
    adapter._history.clear()
    adapter._hub_rtts.clear()
    adapter._publication_error = None
    adapter._api_unsupported = False
    adapter._reset_link()
    adapter._latest_state = {"status": "configured", "last_message": ""}
    yield
    adapter._running = False
    adapter._generation += 1


def frame(role: str, seq: int, values: dict[str, object], t: float = 1.25) -> str:
    return json.dumps({"type": "frame", "role": role, "device": f"{role}_hub",
                       "seq": seq, "t": t, "values": values}, separators=(",", ":"))


def active() -> dict[str, Outlet]:
    outlets = {key: Outlet() for key in adapter.STREAM_CONTRACTS}
    adapter._lsl_outlets = outlets
    adapter._running = True
    adapter._link_opened()
    return outlets


def row(stream: str, sample: list) -> dict[str, float]:
    return dict(zip(adapter.STREAM_CONTRACTS[stream]["channels"], sample))


# ------------------------------------------------------------ data model

def test_recorded_channels_are_the_hub_codec_topics_plus_bookkeeping():
    for stream, topics in HUB_TOPICS.items():
        channels = adapter.STREAM_CONTRACTS[stream]["channels"]
        assert channels == [address.rsplit("/", 1)[-1] for address in topics] + ["rssi", "seq", "hub_timestamp"]
        assert adapter.STREAM_CONTRACTS[stream]["sequence_channel"] == "seq"
    assert adapter.STREAM_CONTRACTS["hub_events"]["channel_format"] == "string"


def test_a_board_frame_is_exactly_one_sample_in_its_stream_with_its_own_values():
    outlets = active()
    adapter._handle_sse_event(frame("radar", 7, {"/sensor/presMoveEnergy": 0, "/sensor/t1speed": -12,
                                                "/sensor/rssiRadar": -61}, t=1700000000.125))
    adapter._handle_sse_event(frame("bio", 3, {"/sensor/heartBpm": 72, "/sensor/bioDist": 85}))
    adapter._handle_sse_event(frame("solenoid", 1, {"/solenoid/CH3": 1}))
    radar, bio, valves = (outlets[key].samples for key in ("radar", "bio", "valves"))
    assert len(radar) == len(bio) == len(valves) == 1
    radar_row = row("radar", radar[0])
    # As the ESP sent them: no unit conversion, 0 stays 0 (see README).
    assert radar_row["presMoveEnergy"] == 0 and radar_row["t1speed"] == -12
    assert radar_row["rssi"] == -61 and radar_row["seq"] == 7
    assert radar_row["hub_timestamp"] == 1700000000.125
    assert math.isnan(radar_row["personDist"])  # not in this frame: missing, never 0
    assert row("bio", bio[0])["bioDist"] == 85
    assert row("valves", valves[0])["CH3"] == 1 and math.isnan(row("valves", valves[0])["CH0"])
    assert outlets["hub_events"].samples == []


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
    assert outlets["hub_events"].samples == [[event] for event in events]
    assert all(outlets[key].samples == [] for key in ("radar", "bio", "valves"))
    assert adapter.get_status()["data_quality"]["hub_dropped_events"] == 2


def test_a_frame_with_a_value_its_stream_does_not_declare_is_also_kept_whole():
    outlets = active()
    event = frame("solenoid", 1, {"/solenoid/CH0": 1, "/solenoid/alive": 1})
    adapter._handle_sse_event(event)
    assert len(outlets["valves"].samples) == 1
    assert outlets["hub_events"].samples == [[event]]
    assert "/solenoid/alive" in adapter.get_status()["unknown_topics"]


def test_no_frame_means_no_sample_and_no_fixed_clock():
    outlets = active()
    assert all(outlet.samples == [] for outlet in outlets.values())
    assert not hasattr(adapter, "_publish_loop")


def test_a_failed_push_is_an_acquisition_error_until_the_next_start():
    adapter._lsl_outlets = {"radar": Outlet(fail=True)}
    adapter._running = True
    adapter._link_opened()
    with pytest.raises(RuntimeError, match="publication failed"):
        adapter._handle_sse_event(frame("radar", 1, {"/sensor/presMoveEnergy": 2}))
    assert adapter.get_status()["status"] == "failed"


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
    active()
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presMoveEnergy": 0, "/sensor/t1speed": 2}))
    adapter._handle_sse_event(frame("bio", 1, {"/sensor/heartBpm": 0, "/sensor/breathRate": 12}))
    status = adapter.get_status()
    assert status["latest"]["presMoveEnergy"] == 0 and status["latest"]["heartBpm"] == 0
    assert len(status["preview"]["movement"]) == 1 and len(status["preview"]["vitals"]) == 1
    adapter._handle_sse_event('{"type":"status","devices":{}}')
    assert len(adapter.get_status()["preview"]["movement"]) == 1


def test_sequence_gaps_and_hub_drops_are_counted_separately():
    active()
    adapter._handle_sse_event(frame("radar", 1, {}))
    adapter._handle_sse_event(frame("radar", 4, {}))
    adapter._handle_sse_event('{"type":"gap","dropped":3}')
    quality = adapter.get_status()["data_quality"]
    assert quality["seq_gaps"]["radar"] == 2
    assert quality["hub_dropped_events"] == 3


def test_stop_and_start_forget_the_live_view_but_not_what_was_recorded():
    outlets = active()
    adapter._handle_sse_event(frame("radar", 1, {"/sensor/presMoveEnergy": 17}))
    adapter.stop()
    status = adapter.get_status()
    assert status["latest"] == {} and status["preview"] == {} and status["seconds_since_last_activity"] is None
    with mock.patch.object(adapter, "_sse_loop"), mock.patch.object(adapter, "_ping_loop"):
        adapter.start()
    try:
        status = adapter.get_status()
        assert status["preview"]["movement"] == [] and status["latest"] == {}
        assert adapter._history == type(adapter._history)(maxlen=adapter._history.maxlen)
        assert len(outlets["radar"].samples) == 1
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
    assert [row("bio", r)["heartBpm"] for r in outlets["bio"].samples] == [72, 0, 0, 74]  # XDF keeps the zeros


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


def test_sse_byte_chunks_arrive_as_whole_events():
    outlets = active()
    event = frame("bio", 2, {"/sensor/heartBpm": 70})
    body = f"data: {event}\r\n\r\n: keepalive\r\n\r\ndata: {{\"type\":\"gap\",\"dropped\":1}}\r\n\r\n".encode()

    class Response:
        raw = None

        def iter_content(self, chunk_size=None):
            return iter(body[index:index + 3] for index in range(0, len(body), 3))

    assert adapter._read_sse_events(Response(), adapter._generation)
    assert row("bio", outlets["bio"].samples[0])["heartBpm"] == 70
    assert outlets["hub_events"].samples == [['{"type":"gap","dropped":1}']]


# ------------------------------------------------------------ XDF boundary

def _replay(outlets: dict[str, Outlet]) -> None:
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
            assert channel_format == "double64" and channel_count == 25
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
            return outlets["radar"].samples, [100.0, 100.2]

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
        writer.write_samples(1, [1000.0, 1000.1], outlets["radar"].samples,
                             channel_format="double64", channel_count=len(stream["channels"]))
        writer.write_stream_footer(1, footer)
        writer.close(durable=True)
    finally:
        writer.destroy()
    streams, _header = pyxdf.load_xdf(str(path), synchronize_clocks=False, handle_clock_resets=False,
                                      dejitter_timestamps=False, verbose=False)
    move = stream["channels"].index("presMoveEnergy")
    assert [values[move] for values in streams[0]["time_series"]] == [0, 5]
