"""The clock core: one set of rules for producing and comparing times."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from study_runner.clock_core import assessment, contract  # noqa: E402
from study_runner.clock_core.producer import (  # noqa: E402
    SourceClock,
    SourceToLsl,
    WallToLsl,
    callback_batch_start,
)

INTERVAL = 1.0 / 250.0


class _Counter:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class CallbackTimelineTests(unittest.TestCase):
    def _batch(self, previous: float | None, receipt: float, steps: int = 3) -> tuple[float, float, float | None]:
        return callback_batch_start(
            received_epoch=receipt,
            within_batch_steps=steps,
            previous_timestamp=previous,
            first_step=1,
            sample_interval=INTERVAL,
        )

    def test_jitter_keeps_a_smooth_timeline(self) -> None:
        start, spacing, jump = self._batch(previous=100.0, receipt=100.0 + 4 * INTERVAL + 0.03)
        self.assertIsNone(jump)
        self.assertAlmostEqual(spacing, INTERVAL * (1 + contract.CALLBACK_MAX_SLEW_FRACTION))
        self.assertAlmostEqual(start, 100.0 + spacing)

    def test_a_pause_reanchors_on_receipt_instead_of_backdating(self) -> None:
        for pause in (2.0, 30.0):
            with self.subTest(pause=pause):
                receipt = 100.0 + pause
                start, spacing, jump = self._batch(previous=100.0, receipt=receipt)
                self.assertAlmostEqual(start + 3 * spacing, receipt)
                self.assertAlmostEqual(jump, pause - 4 * INTERVAL, places=6)

    def test_a_counter_leap_never_dates_a_sample_after_its_receipt(self) -> None:
        # Counter claims 1000 missing frames, but the batch arrived 16 ms later.
        start, spacing, jump = callback_batch_start(
            received_epoch=100.016,
            within_batch_steps=3,
            previous_timestamp=100.0,
            first_step=1000,
            sample_interval=INTERVAL,
        )
        self.assertAlmostEqual(start + 3 * spacing, 100.016)
        self.assertGreater(start, 100.0)
        self.assertLess(jump, 0)

    def test_a_device_faster_than_nominal_never_drifts_or_warns(self) -> None:
        # BrainBit measured in a real session: 250.47 Hz for a nominal 250 Hz,
        # delivered in callbacks of 8 frames with up to 15 ms arrival jitter.
        import random

        rng = random.Random(7)
        true_rate = 250.47
        previous: float | None = None
        sample = 0
        worst_lag = 0.0
        spacings = []
        for _ in range(int(600 * true_rate / 8)):
            sample += 8
            receipt = sample / true_rate + 0.02 + rng.random() * 0.015
            start, spacing, jump = callback_batch_start(
                received_epoch=receipt,
                within_batch_steps=7,
                previous_timestamp=previous,
                first_step=1,
                sample_interval=INTERVAL,
            )
            if previous is not None:
                self.assertIsNone(jump)
                spacings.append(spacing)
            previous = start + 7 * spacing
            worst_lag = max(worst_lag, abs(receipt - previous))
        self.assertLess(worst_lag, 0.05)
        self.assertLessEqual(max(abs(value / INTERVAL - 1) for value in spacings), contract.CALLBACK_MAX_SLEW_FRACTION + 1e-12)


class SourceClockTests(unittest.TestCase):
    def test_source_times_map_onto_the_lsl_clock_without_the_wall_clock(self) -> None:
        counter = _Counter(50.0)
        source = SourceClock(counter=counter, wall=lambda: 1_800_000_000.0)
        counter.value = 52.5
        source_now = source.now()
        mapping = SourceToLsl(source.anchors(), lsl_clock=lambda: 1000.0, counter=_Counter(52.5))
        self.assertAlmostEqual(mapping.to_lsl([source_now])[0], 1000.0)

    def test_an_unknown_counter_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            SourceToLsl({"epoch_anchor": 1.0, "counter_anchor": 1.0, "counter": "monotonic"}, lsl_clock=lambda: 0.0)

    def test_wall_mapping_is_read_on_a_tick_edge(self) -> None:
        # A wall clock that only ticks every 15.625 ms, like time.time on Windows.
        counter = _Counter(0.0)

        def lsl() -> float:
            counter.value += 0.001
            return 500.0 + counter.value

        def wall() -> float:
            return 1000.0 + (counter.value // 0.015625) * 0.015625

        mapping = WallToLsl(lsl_clock=lsl, wall=wall)
        # Exact relation: lsl = wall_true + (500 - 1000), so the offset is -500.
        self.assertAlmostEqual(mapping.offset, -500.0, delta=0.0015)
        self.assertIsNone(mapping.check_step())


class LiveAssessmentTests(unittest.TestCase):
    def test_coverage_uses_the_correction_when_known(self) -> None:
        item = {"clock_status": "corrected", "clock_correction_seconds": 2.0, "last_timestamp": 99.0}
        self.assertEqual(assessment.marker_coverage(item, 100.5), (True, contract.BASIS_CORRECTED))
        self.assertEqual(assessment.start_lag_seconds(item, 101.5), 0.5)

    def test_without_correction_only_receipt_is_compared(self) -> None:
        item = {"clock_status": "uncertain", "last_timestamp": 500.0, "last_receipt_lsl": 99.0}
        self.assertEqual(assessment.marker_coverage(item, 100.0), (False, contract.BASIS_RECEIPT))
        self.assertIsNone(assessment.start_lag_seconds(item, 100.0))

    def test_live_and_offline_agree_on_the_same_recording(self) -> None:
        raw = [10.0 + index * INTERVAL for index in range(1000)]
        correction = 3.25
        marker_end = raw[-1] + correction - INTERVAL / 2
        live = {"clock_status": "corrected", "clock_correction_seconds": correction, "last_timestamp": raw[-1]}
        aligned = [value + correction for value in raw]
        self.assertTrue(assessment.marker_coverage(live, marker_end)[0])
        self.assertGreaterEqual(aligned[-1], marker_end)
        # The last sample lies after the end marker, so it is outside the window.
        self.assertEqual(assessment.window_sample_count(aligned, aligned[0], marker_end), 999)


class OfflineAssessmentTests(unittest.TestCase):
    def test_alignment_status(self) -> None:
        self.assertEqual(
            assessment.alignment_status(recorder_local=True, clock_offset_count=0, synchronized_available=False),
            contract.LOCAL,
        )
        self.assertEqual(
            assessment.alignment_status(recorder_local=False, clock_offset_count=3, synchronized_available=True),
            contract.CORRECTED,
        )
        self.assertEqual(
            assessment.alignment_status(recorder_local=False, clock_offset_count=0, synchronized_available=True),
            contract.UNCERTAIN,
        )

    def test_window_counts_only_samples_inside_the_markers(self) -> None:
        self.assertEqual(assessment.window_sample_count([1.0, 2.0, 3.0, 4.0], 2.0, 3.5), 2)

    def test_a_reanchored_pause_is_a_discontinuity(self) -> None:
        stamps = [0.0, INTERVAL, 2 * INTERVAL, 30.0, 30.0 + INTERVAL]
        jumps = assessment.timeline_discontinuities(stamps, 250.0)
        self.assertEqual(jumps["count"], 1)
        self.assertAlmostEqual(jumps["largest_seconds"], 30.0 - 2 * INTERVAL, places=6)
        self.assertEqual(assessment.timeline_discontinuities(stamps, 0.0)["count"], 0)

    def test_corrections_are_summarised_not_merged(self) -> None:
        summary = assessment.correction_summary([0.0001, -0.0002, 0.00005])
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["min_seconds"], -0.0002)
        self.assertEqual(assessment.correction_summary([])["count"], 0)

    def test_display_offset_comes_from_the_markers_and_is_robust_to_one_outlier(self) -> None:
        lsl = [18629.776, 18630.072, 18636.773, 18759.292]
        server_ms = [(value + 1_791_276_103.165) * 1000 for value in lsl]
        server_ms[1] += 400.0  # one marker pushed late
        offset = assessment.display_epoch_offset(lsl, server_ms)
        self.assertAlmostEqual(offset, 1_791_276_103.165, places=3)
        self.assertIsNone(assessment.display_epoch_offset([1.0], [None]))

    def test_clock_report_records_the_declared_timestamp_source(self) -> None:
        report = assessment.build_clock_report(
            session_id="s",
            declared_streams={"brainbit": [{"source_id": "eeg", "timing": {"timestamp_source": "host_callback_reconstructed"}}]},
            stream_metrics=[{"source_key": "brainbit", "source_id": "eeg", "clock_alignment": "corrected"}],
            start_barrier={"clock_warnings": []},
            end_barrier={"reached": True},
            marker_window={"start": 1.0, "end": 2.0},
        )
        self.assertEqual(report["schema"], assessment.CLOCK_REPORT_SCHEMA)
        self.assertEqual(report["streams"][0]["timestamp_source"], "host_callback_reconstructed")


if __name__ == "__main__":
    unittest.main()
