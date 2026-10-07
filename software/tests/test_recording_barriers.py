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
        "clock_status": "corrected",
        "clock_correction_seconds": 0.0,
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

    def test_recent_arrival_with_old_lsl_timestamp_fails_the_start(self) -> None:
        clock = _Clock()
        health = _health([_stream("eeg", 250, count=100, age=0.01, last=70.0, primary=True)])
        health["lsl_now"] = 100.0
        with self.assertRaisesRegex(RecordingRuntimeError, "30.000s from the LSL clock"):
            wait_for_required_worker_sources(
                _Client(health),
                session_id="s",
                generation=1,
                manifests=MANIFESTS,
                required_sources={"brainbit"},
                timeout_seconds=0.2,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )

    def test_a_silent_regular_stream_blocks_the_default_start_policy(self) -> None:
        clock = _Clock()
        client = _Client(
            _health([_stream("eeg", 250, count=100, age=0.01), _stream("mental", 25, count=0)])
        )
        with self.assertRaisesRegex(RecordingRuntimeError, "brainbit.mental: no sample recorded yet"):
            wait_for_required_worker_sources(
                client,
                session_id="s",
                generation=1,
                manifests=MANIFESTS,
                required_sources={"brainbit"},
                timeout_seconds=0.2,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )

    def test_manifest_primary_only_policy_keeps_secondary_as_a_warning(self) -> None:
        clock = _Clock()
        manifests = {
            "brainbit": {
                **MANIFESTS["brainbit"],
                "capability_config": {"recording_source": {"start_sample_policy": "primary_only"}},
            }
        }
        health = wait_for_required_worker_sources(
            _Client(_health([_stream("eeg", 250, count=100, age=0.01, primary=True), _stream("mental", 25)])),
            session_id="s",
            generation=1,
            manifests=manifests,
            required_sources={"brainbit"},
            secondary_grace_seconds=0.2,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertEqual(health["late_streams"], ["brainbit.mental: no sample recorded yet"])

    def test_event_only_source_needs_headers_but_no_boundary_sample(self) -> None:
        clock = _Clock()
        health = {
            "readiness_contract": "fresh-primary/v1",
            "frozen": False,
            "sources": {"events": {"streams": [_stream("emotion", 0, primary=True)]}},
        }
        result = wait_for_required_worker_sources(
            _Client(health),
            session_id="s",
            generation=1,
            manifests={"events": {"capabilities": ["study_sensor"]}},
            required_sources={"events"},
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        self.assertEqual(result["sources"], health["sources"])
        self.assertEqual(result["clock_warnings"], [])


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

    def test_remote_clock_correction_is_used_for_end_coverage(self) -> None:
        stream = _stream("eeg", 250, last=self.MARKER - 10.0)
        stream["clock_correction_seconds"] = 10.1
        self.assertEqual(
            stream_tail_issues({"brainbit": {"streams": [stream]}}, {"brainbit"}, marker_lsl_timestamp=self.MARKER),
            [],
        )

    def test_without_correction_only_the_recorder_receipt_time_is_compared(self) -> None:
        # The outlet timestamp (30 s behind) is on an unknown clock, so it is
        # not compared at all; receipt after the marker is the only evidence.
        stream = _stream("eeg", 250, count=100, age=0.01, last=self.MARKER - 30.0)
        stream.update(clock_status="uncertain", clock_correction_seconds=None, last_receipt_lsl=self.MARKER + 0.2)
        result = wait_for_stream_tail(
            _Client(_health([stream])), session_id="s", generation=1,
            required_sources={"brainbit"}, marker_lsl_timestamp=self.MARKER,
        )
        self.assertTrue(result["reached"])
        self.assertEqual(result["lagging_streams"], [])
        self.assertEqual(result["receipt_only_streams"], ["brainbit.eeg"])

    def test_without_correction_and_without_later_receipt_the_stream_lags(self) -> None:
        clock = _Clock()
        stream = _stream("eeg", 250, count=100, age=0.01, last=self.MARKER + 5.0)
        stream.update(clock_status="uncertain", clock_correction_seconds=None, last_receipt_lsl=self.MARKER - 0.5)
        result = wait_for_stream_tail(
            _Client(_health([stream])), session_id="s", generation=1,
            required_sources={"brainbit"}, marker_lsl_timestamp=self.MARKER,
            timeout_seconds=0.3, monotonic=clock.monotonic, sleeper=clock.sleep,
        )
        self.assertFalse(result["reached"])
        self.assertEqual(result["lagging_streams"], ["brainbit.eeg"])

    def test_corrected_remote_clock_is_used_for_start_readiness(self) -> None:
        stream = _stream("eeg", 250, count=100, age=0.01, last=90.0, primary=True)
        stream["clock_correction_seconds"] = 10.0
        health = _health([stream])
        health["lsl_now"] = 100.0
        result = wait_for_required_worker_sources(
            _Client(health), session_id="s", generation=1,
            manifests=MANIFESTS, required_sources={"brainbit"},
        )
        self.assertEqual(result["clock_warnings"], [])

    def test_start_waits_for_the_first_clock_correction(self) -> None:
        clock = _Clock()
        uncorrected = _stream("eeg", 250, count=100, age=0.01, last=99.9, primary=True)
        uncorrected.update(clock_status="uncertain", clock_correction_seconds=None)
        corrected = _stream("eeg", 250, count=120, age=0.01, last=99.95, primary=True)
        client = _Client(_health([uncorrected]), _health([uncorrected]), _health([corrected]))
        result = wait_for_required_worker_sources(
            client, session_id="s", generation=1, manifests=MANIFESTS, required_sources={"brainbit"},
            monotonic=clock.monotonic, sleeper=clock.sleep,
        )
        self.assertEqual(result["clock_warnings"], [])
        self.assertEqual(client.calls, 3)

    def test_a_correction_still_missing_at_the_deadline_starts_with_a_warning(self) -> None:
        clock = _Clock()
        stream = _stream("eeg", 250, count=100, age=0.01, last=99.9, primary=True)
        stream.update(clock_status="uncertain", clock_correction_seconds=None)
        health = _health([stream])
        health["lsl_now"] = 100.0
        result = wait_for_required_worker_sources(
            _Client(health), session_id="s", generation=1, manifests=MANIFESTS,
            required_sources={"brainbit"}, timeout_seconds=0.5,
            monotonic=clock.monotonic, sleeper=clock.sleep,
        )
        self.assertEqual(result["clock_warnings"], ["brainbit.eeg: LSL clock correction uncertain"])

    def test_missing_worker_source_cannot_report_a_reached_tail(self) -> None:
        self.assertEqual(
            stream_tail_issues({}, {"brainbit"}, marker_lsl_timestamp=self.MARKER),
            ["brainbit: worker has no source state"],
        )
        self.assertEqual(
            stream_tail_issues({"brainbit": {"streams": []}}, {"brainbit"}, marker_lsl_timestamp=self.MARKER),
            ["brainbit: worker has no stream state"],
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
