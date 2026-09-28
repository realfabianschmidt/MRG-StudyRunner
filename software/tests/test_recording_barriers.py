"""Start and end barriers around a participant session's recording.

A real session on 2026-09-28 failed its time-coverage check although every
sample was recorded: the band-power stream's first sample arrived 52 ms after
the study-start marker, because the start gate only waited for the primary
EEG stream. These tests pin the fix: the start marker waits for every regular
stream, and the freeze waits until every regular stream passed the end marker.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.data_core.host.recording_runtime_support import (
    RecordingRuntimeError,
    regular_stream_readiness_issues,
    stream_tail_issues,
    wait_for_required_worker_sources,
    wait_for_stream_tail,
)


class _Response:
    def __init__(self, result):
        self.ok = True
        self.error = None
        self.result = result


class _Client:
    """Returns one worker health payload per call, repeating the last one."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def send(self, command, payload, *, command_id):
        self.calls += 1
        index = min(self.calls - 1, len(self.payloads) - 1)
        return _Response(self.payloads[index])


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _stream(key, rate, *, count=0, age=None, last=None, primary=False):
    return {
        "key": key,
        "nominal_rate_hz": rate,
        "primary": primary,
        "header_written": True,
        "sample_count": count,
        "last_sample_age_seconds": age,
        "last_timestamp": last,
    }


def _health(streams):
    return {
        "readiness_contract": "fresh-primary/v1",
        "frozen": False,
        "sources": {"brainbit": {"streams": streams}},
    }


MANIFESTS = {"brainbit": {"capabilities": ["study_sensor"], "streams": []}}


class StartBarrierTests(unittest.TestCase):
    def test_a_derived_stream_without_a_sample_blocks_the_start_marker(self) -> None:
        issues = regular_stream_readiness_issues(
            "brainbit",
            [
                _stream("eeg", 250, count=120, age=0.01, primary=True),
                _stream("bands", 25, count=0),
                _stream("quality", 0),
            ],
        )
        self.assertEqual(issues, ["brainbit.bands: no sample recorded yet"])

    def test_irregular_event_streams_never_block(self) -> None:
        issues = regular_stream_readiness_issues(
            "brainbit",
            [_stream("battery", 0), _stream("diagnostics", 0)],
        )
        self.assertEqual(issues, [])

    def test_the_gate_waits_until_the_late_band_stream_has_its_first_sample(self) -> None:
        # First poll: the recorded session's situation (EEG flowing, bands not
        # yet). Second poll: bands have arrived. Only then may study_start go.
        clock = _Clock()
        client = _Client(
            _health([_stream("eeg", 250, count=100, age=0.01), _stream("bands", 25, count=0)]),
            _health([_stream("eeg", 250, count=130, age=0.01), _stream("bands", 25, count=1, age=0.01)]),
        )
        wait_for_required_worker_sources(
            client,
            session_id="s",
            generation=1,
            manifests=MANIFESTS,
            required_sources={"brainbit"},
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertEqual(client.calls, 2)

    def test_a_missing_primary_stream_fails_the_start(self) -> None:
        clock = _Clock()
        client = _Client(
            _health([_stream("eeg", 250, count=0, primary=True), _stream("bands", 25, count=3, age=0.01)])
        )
        with self.assertRaisesRegex(RecordingRuntimeError, "brainbit.eeg: no sample recorded yet"):
            wait_for_required_worker_sources(
                client,
                session_id="s",
                generation=1,
                manifests=MANIFESTS,
                required_sources={"brainbit"},
                timeout_seconds=1.0,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )

    def test_a_silent_derived_stream_is_reported_after_a_short_grace(self) -> None:
        # Before calibration a headband sends raw EEG but no derived values.
        # The start must not be refused for that; the silence is reported.
        clock = _Clock()
        client = _Client(
            _health([_stream("eeg", 250, count=100, age=0.01), _stream("mental", 25, count=0)])
        )
        health = wait_for_required_worker_sources(
            client,
            session_id="s",
            generation=1,
            manifests=MANIFESTS,
            required_sources={"brainbit"},
            secondary_grace_seconds=2.0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertEqual(health["late_streams"], ["brainbit.mental: no sample recorded yet"])
        self.assertGreaterEqual(clock.now, 2.0)


class EndBarrierTests(unittest.TestCase):
    MARKER = 8510.5213

    def test_streams_before_the_end_marker_are_reported(self) -> None:
        sources = {
            "brainbit": {
                "streams": [
                    _stream("eeg", 250, last=8510.60),
                    _stream("bands", 25, last=8510.49),
                    _stream("quality", 0, last=None),
                ]
            }
        }
        self.assertEqual(
            stream_tail_issues(sources, {"brainbit"}, marker_lsl_timestamp=self.MARKER),
            ["brainbit.bands"],
        )

    def test_the_freeze_waits_until_every_stream_passed_the_marker(self) -> None:
        clock = _Clock()
        client = _Client(
            _health([_stream("eeg", 250, last=8510.60), _stream("bands", 25, last=8510.49)]),
            _health([_stream("eeg", 250, last=8510.70), _stream("bands", 25, last=8510.53)]),
        )
        result = wait_for_stream_tail(
            client,
            session_id="s",
            generation=1,
            required_sources={"brainbit"},
            marker_lsl_timestamp=self.MARKER,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertTrue(result["reached"])
        self.assertEqual(client.calls, 2)

    def test_a_timeout_reports_the_lagging_stream_instead_of_blocking(self) -> None:
        clock = _Clock()
        client = _Client(_health([_stream("bands", 25, last=8510.49)]))
        result = wait_for_stream_tail(
            client,
            session_id="s",
            generation=1,
            required_sources={"brainbit"},
            marker_lsl_timestamp=self.MARKER,
            timeout_seconds=0.5,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertFalse(result["reached"])
        self.assertEqual(result["lagging_streams"], ["brainbit.bands"])


if __name__ == "__main__":
    unittest.main()
