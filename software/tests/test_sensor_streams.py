"""SensorStreams: the one way a sensor plugin publishes data (sensor data contract)."""
from __future__ import annotations

import math
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fake_lsl import FakePylsl  # noqa: E402

from study_runner.contracts.manifest import validate_and_normalize_manifest  # noqa: E402
from study_runner.contracts.plugin_api import Plugin  # noqa: E402
from study_runner.contracts.sensor_contract import LIVE_VIEW_POINTS  # noqa: E402
from study_runner.plugin_framework import driver_runtime, registry, sensor_streams  # noqa: E402
from study_runner.plugin_framework.sensor_streams import SensorStreams  # noqa: E402


def sensor_manifest(**changes) -> dict:
    payload = {
        "api_version": 5,
        "plugin_key": "fixture_sensor",
        "config_key": "fixture_sensor",
        "version": "1.0.0",
        "category": "biosignal",
        "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1", "actions": ["start", "stop"]},
        "ui": {"label": "Fixture sensor"},
        "capabilities": {
            "study_sensor": {},
            "lsl_stream_provider": {},
            "recording_source": {"artifact": "xdf", "primary_stream": "values"},
            "backup_projection": {"channels": [{"output": "value", "stream": "values", "channel": "value"}]},
            "live_view": {"series": [{"key": "trend", "stream": "values", "channels": ["value", "other"]}]},
            "health": {},
        },
        "streams": [
            {
                "key": "values",
                "source_id": "study_runner.fixture_sensor.values",
                "type": "VALUES",
                "nominal_rate_hz": 10,
                "clock_domain": "lsl",
                "channel_format": "double64",
                "channels": ["value", "other", "seq", "correction_ms"],
                "channel_units": ["arbitrary_unit", "arbitrary_unit", "count", "millisecond"],
                "sequence_channel": "seq",
                "timing": {"timestamp_source": "host_arrival_corrected", "correction_channel": "correction_ms"},
            },
            {
                "key": "events",
                "source_id": "study_runner.fixture_sensor.events",
                "type": "EVENTS",
                "nominal_rate_hz": 0,
                "clock_domain": "lsl",
                "channel_format": "string",
                "channels": ["event"],
                "channel_units": ["json"],
                "timing": {"timestamp_source": "host_arrival"},
            },
            {
                "key": "device",
                "source_id": "study_runner.fixture_sensor.device",
                "type": "DEVICE",
                "nominal_rate_hz": 250,
                "clock_domain": "lsl",
                "channel_format": "float32",
                "channels": ["ch1", "ch2"],
                "channel_units": ["microvolt", "microvolt"],
                "timing": {"timestamp_source": "host_callback_reconstructed"},
            },
        ],
    }
    payload.update(changes)
    return validate_and_normalize_manifest(payload, directory_name="fixture_sensor")


@pytest.fixture
def lsl() -> FakePylsl:
    return FakePylsl(clock=500.0)


@pytest.fixture
def streams(lsl: FakePylsl) -> SensorStreams:
    instance = SensorStreams(sensor_manifest(), backend=lsl)
    instance.configure(name_prefix="Fixture")
    assert instance.open_all()
    return instance


# ------------------------------------------------------------- outlets

def test_outlets_are_built_from_the_manifest_only(streams: SensorStreams, lsl: FakePylsl) -> None:
    info = lsl.outlet("study_runner.fixture_sensor.values").info
    assert (info.name, info.type, info.channel_count, info.nominal_srate, info.channel_format) == (
        "Fixture_VALUES", "VALUES", 4, 10.0, "double64",
    )
    assert info.channel_labels() == ["value", "other", "seq", "correction_ms"]
    assert info.channel_units() == ["arbitrary_unit", "arbitrary_unit", "count", "millisecond"]
    desc = info.study_runner()
    assert desc["timestamp_source"] == "host_arrival_corrected"
    assert desc["correction_channel"] == "correction_ms"
    assert desc["source_id"] == "study_runner.fixture_sensor.values"


def test_opening_again_keeps_the_outlet_unless_the_channels_change(streams: SensorStreams, lsl: FakePylsl) -> None:
    before = len(lsl.outlets)
    assert streams.open("device")
    assert len(lsl.outlets) == before

    assert streams.open("device", channels=["O1", "O2", "T3", "T4"])
    assert len(lsl.outlets) == before + 1
    info = lsl.outlet("study_runner.fixture_sensor.device").info
    assert info.channel_labels() == ["O1", "O2", "T3", "T4"]
    # One unit for every declared channel: it carries over to runtime channels.
    assert info.channel_units() == ["microvolt"] * 4
    contract = next(item for item in streams.contracts() if item["key"] == "device")
    assert contract["channels"] == ["O1", "O2", "T3", "T4"]
    assert contract["source_id"] == "study_runner.fixture_sensor.device"


def test_a_missing_pylsl_is_a_counted_failure_not_a_crash() -> None:
    instance = SensorStreams(sensor_manifest())
    instance._backend_missing = True
    assert instance.open("values") is False
    health = instance.status_blocks()["stream_health"]
    assert health["failed"] is True
    assert "pylsl" in health["last_error"]


# ----------------------------------------------------------- publishing

def test_every_sample_carries_an_explicit_timestamp_that_never_goes_backwards(streams: SensorStreams, lsl: FakePylsl) -> None:
    outlet = lsl.outlet("study_runner.fixture_sensor.events")
    for stamp in (10.0, 12.0, 11.0, 13.0):
        assert streams.push("events", [f"e{stamp}"], stamp)
    assert outlet.timestamps == [10.0, 12.0, 12.0, 13.0]
    assert streams.status_blocks()["stream_health"]["streams"]["events"]["clamped"] == 1


def test_a_correction_stays_reversible(streams: SensorStreams, lsl: FakePylsl) -> None:
    outlet = lsl.outlet("study_runner.fixture_sensor.values")
    row = streams.row("values", {"value": 1.0, "other": 2.0, "seq": 1})
    streams.push("values", row, 99.95, arrival=100.0)
    # A timestamp after the arrival is impossible: it is lowered to the arrival.
    streams.push("values", streams.row("values", {"value": 3.0, "seq": 2}), 101.5, arrival=101.0)
    # Without a correction the arrival is the timestamp.
    streams.push("values", streams.row("values", {"value": 4.0, "seq": 3}), 102.0)
    (first, t1), (second, t2), (third, t3) = outlet.samples
    assert (t1, t2, t3) == (99.95, 101.0, 102.0)
    assert first[3] == pytest.approx(50.0)
    assert t1 + first[3] / 1000.0 == pytest.approx(100.0)
    assert second[3] == 0.0 and third[3] == 0.0
    assert math.isnan(second[1])  # "other" missing: NaN, never 0


def test_a_failed_push_is_counted_and_reported_never_raised(streams: SensorStreams, lsl: FakePylsl) -> None:
    lsl.outlet("study_runner.fixture_sensor.events").fail = OSError("LSL down")
    assert streams.push("events", ["x"], 1.0) is False
    assert streams.push("events", ["y"], 2.0) is False
    health = streams.status_blocks()["stream_health"]
    assert health["failed"] is True
    assert health["streams"]["events"]["failures"] == 2
    assert "LSL down" in health["last_error"]

    streams.reset()
    assert streams.status_blocks()["stream_health"]["failed"] is False
    assert streams.status_blocks()["stream_health"]["streams"]["events"]["open"] is True


def test_a_wrong_row_width_or_a_closed_stream_is_a_failure(streams: SensorStreams) -> None:
    assert streams.push("values", [1.0], 1.0) is False
    streams.close("events")
    assert streams.push("events", ["x"], 2.0) is False
    health = streams.status_blocks()["stream_health"]["streams"]
    assert health["values"]["failures"] == 1
    assert health["events"]["failures"] == 1 and health["events"]["open"] is False


def test_a_chunk_keeps_each_timestamp(streams: SensorStreams, lsl: FakePylsl) -> None:
    rows = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    assert streams.push_chunk("device", rows, [1.0, 1.004, 1.008])
    assert lsl.outlet("study_runner.fixture_sensor.device").timestamps == [1.0, 1.004, 1.008]


def test_rows_follow_the_channel_order(streams: SensorStreams) -> None:
    row = streams.row("values", {"seq": 7, "value": "1.5", "other": None})
    assert row[0] == 1.5 and math.isnan(row[1]) and row[2] == 7.0
    assert streams.row("events", {"event": {"a": 1}}) == ["{'a': 1}"]
    assert streams.row("events", {}) == [""]


def test_a_rejected_sample_is_counted_but_is_no_failure(streams: SensorStreams) -> None:
    streams.reject("values", "duplicate sequence")
    health = streams.status_blocks()["stream_health"]
    assert health["failed"] is False
    assert health["streams"]["values"]["rejected"] == 1
    assert health["streams"]["values"]["last_rejection"] == "duplicate sequence"


# ------------------------------------------------------------ live view

def test_every_push_feeds_the_live_view_and_reset_empties_it(lsl: FakePylsl) -> None:
    clock = [100.2]
    instance = SensorStreams(sensor_manifest(), backend=lsl)
    instance._live._clock = lambda: clock[0]
    instance.open_all()
    instance.push("values", instance.row("values", {"value": 2.0, "other": 4.0}), 1.0)
    instance.push("values", instance.row("values", {"value": 4.0}), 1.1)
    clock[0] = 100.7  # the 100.0-100.5 bucket is complete now
    live = instance.status_blocks()["live"]
    trend = live["series"]["trend"]
    assert live["points"] == LIVE_VIEW_POINTS
    assert trend["channels"]["value"][-1] == 3.0
    assert trend["channels"]["other"][-1] == 4.0
    assert trend["valid"] is None

    instance.reset()
    assert all(value is None for value in instance.status_blocks()["live"]["series"]["trend"]["channels"]["value"])


# ------------------------------------------------------------- the core

def _fixture_plugin(status: dict) -> Plugin:
    return Plugin(
        key="fixture_sensor",
        label="Fixture sensor",
        category="biosignal",
        config_key="fixture_sensor",
        can_start=True,
        get_status=lambda context: dict(status),
        start=lambda context: {"started": True},
    )


def test_the_driver_merges_live_view_and_health_and_resets_on_start(lsl: FakePylsl) -> None:
    instance = SensorStreams(sensor_manifest(), backend=lsl)
    instance.open_all()
    with mock.patch.dict(sensor_streams._REGISTRY, {"fixture_sensor": instance}):
        lsl.outlet("study_runner.fixture_sensor.events").fail = OSError("LSL down")
        instance.push("events", ["x"], 1.0)
        plugin = _fixture_plugin({"status": "connected", "running": True})
        status, _ = driver_runtime._dispatch(plugin, SimpleNamespace(), "status", {})
        assert status["status"] == "connected"
        assert status["stream_health"]["failed"] is True
        assert set(status["live"]["series"]) == {"trend"}

        driver_runtime._dispatch(plugin, SimpleNamespace(), "start", {})
        assert instance.status_blocks()["stream_health"]["failed"] is False


def test_the_host_standardizes_the_live_view_and_reports_a_failed_publication(tmp_path: Path) -> None:
    manifest = sensor_manifest()
    plugin = _fixture_plugin({})
    context = registry.build_context(
        base_dir=tmp_path, data_dir=tmp_path, hardware_config={"fixture_sensor": {"enabled": True}},
        local_secrets={}, local_secrets_file=tmp_path / "secrets.json",
    )
    raw = {
        "status": "connected",
        "running": True,
        "connection": {"phase": "connected", "streaming": True, "signal": {"state": "good"}},
        "live": {"series": {"trend": {"channels": {"value": [1.0, float("nan"), float("inf")], "undeclared": [1]}},
                            "extra": {}}},
        "stream_health": {"failed": True, "last_error": "values publication failed: LSL down"},
    }
    with mock.patch.object(registry, "_PLUGIN_CATALOG", SimpleNamespace(manifests={"fixture_sensor": manifest})):
        status = registry._standardize_status(plugin, context, raw)
        stopped = registry._standardize_status(plugin, context, {**raw, "running": False, "stream_health": {}})

    series = status["live"]["series"]
    assert set(series) == {"trend"}
    assert series["trend"]["channels"]["value"][-3:] == [1.0, None, None]
    assert len(series["trend"]["channels"]["value"]) == LIVE_VIEW_POINTS
    assert series["trend"]["channels"]["other"] == [None] * LIVE_VIEW_POINTS
    assert status["status"] == "failed"
    assert "LSL down" in status["last_message"]
    assert status["connection"]["phase"] == "failed"
    assert status["connection"]["detail"] == "publication_failed"
    assert status["connection"]["next_step"] is None
    assert status["connection"]["ready"] is False
    # Off: the same graphs, empty -- never an old value.
    assert stopped["live"]["series"]["trend"]["channels"]["value"] == [None] * LIVE_VIEW_POINTS


def test_for_plugin_loads_the_manifest_beside_the_adapter_and_registers_it(tmp_path: Path) -> None:
    import json

    folder = tmp_path / "fixture_sensor"
    folder.mkdir()
    raw = json.loads(json.dumps({
        "api_version": 5, "plugin_key": "fixture_sensor", "config_key": "fixture_sensor", "version": "1.0.0",
        "category": "biosignal", "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1"},
        "ui": {"label": "Fixture"}, "capabilities": {"lsl_stream_provider": {}},
        "streams": [{"key": "values", "source_id": "fixture.values", "type": "VALUES", "nominal_rate_hz": 1,
                     "channel_format": "float32", "channels": ["value"], "channel_units": ["arbitrary_unit"]}],
    }))
    (folder / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with mock.patch.dict(sensor_streams._REGISTRY, clear=True):
        instance = SensorStreams.for_plugin(str(folder / "adapter.py"), backend=FakePylsl())
        assert sensor_streams.registered("fixture_sensor") is instance
        assert instance.declared("values")["source_id"] == "fixture.values"
