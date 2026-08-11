from __future__ import annotations

import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from neurosdk.cmn_types import (
    BrainBitSignalData,
    EEGChannelId,
    EEGChannelInfo,
    EEGChannelType,
    SignalChannelsData,
)

from study_runner.plugins.brainbit import adapter
from study_runner.plugins.brainbit import brainbit_realtime_cli as cli
from study_runner.plugins.brainbit import diagnose_backends
from study_runner.plugins.brainbit import driver


class PacketDecoderTests(unittest.TestCase):
    def test_classic_packet_fields_are_decoded_without_processing(self) -> None:
        packet = BrainBitSignalData(PackNum=4, Marker=0, O1=1e-6, O2=2e-6, T3=3e-6, T4=4e-6)

        values, shape = cli._decode_packet_channels(packet)

        self.assertEqual(shape, "classic_fields")
        self.assertEqual(values, {"O1": 1e-6, "O2": 2e-6, "T3": 3e-6, "T4": 4e-6})

    def test_array_packet_uses_supported_channel_num_not_list_order(self) -> None:
        channels = [
            EEGChannelInfo(EEGChannelId.EEGChIdT4, EEGChannelType.EEGChTypeDifferential, "T4", 1),
            EEGChannelInfo(EEGChannelId.EEGChIdO1, EEGChannelType.EEGChTypeDifferential, "O1", 2),
            EEGChannelInfo(EEGChannelId.EEGChIdT3, EEGChannelType.EEGChTypeDifferential, "T3", 3),
            EEGChannelInfo(EEGChannelId.EEGChIdO2, EEGChannelType.EEGChTypeDifferential, "O2", 0),
        ]
        packet = SignalChannelsData(PackNum=7, Marker=0, Samples=[2e-6, 4e-6, 1e-6, 3e-6])

        mapping = cli._supported_channel_index_map(channels)
        values, shape = cli._decode_packet_channels(packet, mapping)

        self.assertEqual(mapping, {"T4": 1, "O1": 2, "T3": 3, "O2": 0})
        self.assertEqual(shape, "samples_array")
        self.assertEqual(values, {"O1": 1e-6, "O2": 2e-6, "T3": 3e-6, "T4": 4e-6})

    def test_array_packet_without_derived_channels_keeps_all_raw_channels(self) -> None:
        packet = SignalChannelsData(PackNum=7, Marker=0, Samples=[1.0, 2.0, 3.0])

        values, shape = cli._decode_packet_channels(packet, {"Fp1": 0, "C3": 1, "C4": 2})

        self.assertEqual(shape, "samples_array")
        self.assertEqual(values, {"Fp1": 1.0, "C3": 2.0, "C4": 3.0})

    def test_all_current_brainbit_families_are_added_when_available(self) -> None:
        class Families:
            LEBrainBit = 1
            LEBrainBitBlack = 2
            LEBrainBit2 = 3
            LEBrainBitPro = 4
            LEBrainBitFlex = 5

        self.assertEqual(cli._brainbit_sensor_families(Families), [1, 2, 3, 4, 5])


class DeviceSelectionSafetyTests(unittest.TestCase):
    def test_missing_configured_target_exits_without_creating_fallback_sensor(self) -> None:
        class Families:
            LEBrainBit = 1
            LEBrainBitBlack = 2
            LEBrainBit2 = 3

        class Info:
            Name = "BrainBit"
            SensFamily = 1
            Address = "AA:BB"
            SerialNumber = "present"
            PairingRequired = False
            RSSI = -50

        class Scanner:
            created = False

            def __init__(self, families) -> None:
                self.sensorsChanged = None

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

            def sensors(self):
                return [Info()]

            def create_sensor(self, info):
                Scanner.created = True
                raise AssertionError("fallback sensor must not be created")

        with (
            mock.patch.object(cli, "_ensure_requirements"),
            mock.patch.object(cli, "_load_sdk_modules"),
            mock.patch.object(cli, "_validate_sdk_api_surface"),
            mock.patch.object(cli, "Scanner", Scanner, create=True),
            mock.patch.object(cli, "SensorFamily", Families, create=True),
            mock.patch.object(cli.time, "sleep"),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["--scan-seconds", "1", "--serial-number", "missing", "--no-osc"])

        self.assertEqual(caught.exception.code, cli.EXIT_DEVICE_TARGET_MISSING)
        self.assertFalse(Scanner.created)


class EmotionalMathContractTests(unittest.TestCase):
    def test_documented_percent_values_are_always_converted_to_ratio(self) -> None:
        self.assertAlmostEqual(cli._percent_to_ratio(0.248), 0.00248)
        self.assertAlmostEqual(cli._percent_to_ratio(1.49), 0.0149)
        self.assertAlmostEqual(cli._percent_to_ratio(1.51), 0.0151)
        self.assertAlmostEqual(cli._percent_to_ratio(99.75), 0.9975)

    def test_push_bipolars_is_preferred_over_legacy_method(self) -> None:
        calls: list[str] = []

        class MathLib:
            def push_bipolars(self, samples):
                calls.append(f"current:{len(samples)}")

            def push_data(self, samples):
                calls.append(f"legacy:{len(samples)}")

        cli._push_bipolar_samples(MathLib(), [1, 2])

        self.assertEqual(calls, ["current:2"])

    def test_legacy_push_data_is_not_silently_accepted(self) -> None:
        class LegacyMath:
            def push_data(self, samples):
                raise AssertionError("legacy API must not be invoked")

        with self.assertRaisesRegex(AttributeError, "must expose push_bipolars"):
            cli._push_bipolar_samples(LegacyMath(), [object()])

    def test_all_declared_spectral_bands_are_retained(self) -> None:
        calls: list[tuple] = []

        class MathLib:
            def set_calibration_length(self, value): calls.append(("calibration", value))
            def set_mental_estimation_mode(self, value): calls.append(("mode", value))
            def set_skip_wins_after_artifact(self, value): calls.append(("skip", value))
            def set_squared_spectrum(self, value): calls.append(("squared", value))
            def set_zero_spect_waves(self, *values): calls.append(("waves", *values))
            def set_spect_normalization_by_bands_width(self, value): calls.append(("normalize", value))

        cli._configure_emotional_math(MathLib(), calibration_sec=6, skip_windows=10)

        self.assertIn(("waves", True, 1, 1, 1, 1, 1), calls)

    def test_startup_accepts_an_api_surface_that_has_push_bipolars(self) -> None:
        class MathLib:
            def push_bipolars(self, samples):
                ...

        class FakeModule:
            EmotionalMath = MathLib

        with mock.patch.object(cli, "emotional_math", FakeModule, create=True):
            cli._validate_sdk_api_surface()

    def test_startup_fails_closed_before_any_device_work_if_push_bipolars_is_gone(self) -> None:
        class DriftedMathLib:
            def push_data(self, samples):
                ...

        class FakeModule:
            EmotionalMath = DriftedMathLib

        buffer = io.StringIO()
        with mock.patch.object(cli, "emotional_math", FakeModule, create=True):
            with redirect_stdout(buffer):
                with self.assertRaises(SystemExit) as raised:
                    cli._validate_sdk_api_surface()

        self.assertEqual(raised.exception.code, cli.EXIT_MISSING_DEPENDENCY)
        self.assertIn("SETUP_FAIL", buffer.getvalue())
        self.assertIn("push_bipolars", buffer.getvalue())

    def test_measured_hz_is_none_before_enough_time_has_elapsed(self) -> None:
        self.assertIsNone(cli._measured_hz(total_samples=0, elapsed_seconds=0.0))
        self.assertIsNone(cli._measured_hz(total_samples=5, elapsed_seconds=0.0))

    def test_measured_hz_is_the_average_since_stream_start(self) -> None:
        self.assertAlmostEqual(cli._measured_hz(total_samples=2500, elapsed_seconds=10.0), 250.0)
        self.assertAlmostEqual(cli._measured_hz(total_samples=1200, elapsed_seconds=5.0), 240.0)

    def test_queue_cap_is_a_no_op_below_the_limit(self) -> None:
        pending = [{"n": index} for index in range(10)]

        dropped = cli._apply_eeg_queue_cap(pending, max_samples=100)

        self.assertEqual(dropped, 0)
        self.assertEqual(len(pending), 10)

    def test_queue_cap_drops_the_oldest_samples_and_reports_the_count(self) -> None:
        pending = [{"n": index} for index in range(120)]

        dropped = cli._apply_eeg_queue_cap(pending, max_samples=100)

        self.assertEqual(dropped, 20)
        self.assertEqual(len(pending), 100)
        # Oldest (lowest index) samples are the ones dropped, not the newest.
        self.assertEqual(pending[0]["n"], 20)
        self.assertEqual(pending[-1]["n"], 119)

    def test_current_lowercase_result_fields_are_read(self) -> None:
        class Spectral:
            delta = 0.1

        class Mental:
            inst_attention = 0.7

        self.assertEqual(cli._result_value(Spectral(), "delta", "Delta"), 0.1)
        self.assertEqual(cli._result_value(Mental(), "inst_attention", "Inst_Attention"), 0.7)


class TimingAndLslTests(unittest.TestCase):
    def setUp(self) -> None:
        adapter._lsl_outlets = {}
        adapter._lsl_local_clock = None
        adapter._eeg_lsl_channels = ()
        adapter._lsl_stream_health = {}

    def tearDown(self) -> None:
        adapter._lsl_outlets = {}
        adapter._lsl_local_clock = None
        adapter._eeg_lsl_channels = ()
        adapter._lsl_stream_health = {}

    def test_reconstructed_batch_timestamps_are_spaced_and_monotonic(self) -> None:
        estimator = cli.SourceTimestampEstimator(250)

        first = estimator.for_batch(4, 100.0)
        second = estimator.for_batch(4, 100.005)

        self.assertAlmostEqual(first[1] - first[0], 0.004)
        self.assertAlmostEqual(second[0] - first[-1], 0.004)
        self.assertEqual(first + second, sorted(first + second))

    def test_derived_backlog_gets_distinct_25_hz_timestamps(self) -> None:
        estimator = cli.SourceTimestampEstimator(25)

        timestamps = estimator.for_batch(150, 106.0)

        self.assertEqual(len(timestamps), 150)
        self.assertAlmostEqual(timestamps[-1], 106.0)
        self.assertTrue(all(later > earlier for earlier, later in zip(timestamps, timestamps[1:])))
        self.assertAlmostEqual(timestamps[1] - timestamps[0], 0.04)

    def test_packet_counter_gap_is_preserved_and_reported(self) -> None:
        estimator = cli.SourceTimestampEstimator(250)

        first, first_events = estimator.for_packets([100, 101], 100.000)
        second, second_events = estimator.for_packets([104, 105], 100.016)

        self.assertEqual(first_events, [{"gap_before": 0}, {"gap_before": 0}])
        self.assertAlmostEqual(second[0] - first[-1], 0.012)
        self.assertEqual(second_events[0]["counter_event"], "gap")
        self.assertEqual(second_events[0]["gap_before"], 2)
        self.assertEqual(estimator.packet_gap_frames_total, 2)

    def test_packet_counter_wrap_does_not_create_a_false_gap(self) -> None:
        estimator = cli.SourceTimestampEstimator(250)

        _, events = estimator.for_packets([254, 255, 0, 1], 100.0)

        self.assertEqual(events[2]["counter_event"], "wrap")
        self.assertEqual(estimator.packet_gap_frames_total, 0)

    def test_eeg_batch_is_pushed_as_chunk_with_explicit_lsl_timestamps(self) -> None:
        class Outlet:
            def __init__(self) -> None:
                self.chunks = []

            def push_chunk(self, values, timestamps) -> None:
                self.chunks.append((values, timestamps))

        outlet = Outlet()
        adapter._lsl_outlets = {"EEG": outlet}
        adapter._eeg_lsl_channels = ("T4", "O1", "T3", "O2")
        adapter._lsl_local_clock = lambda: 500.0
        payload = {
            "channels": ["T4", "O1", "T3", "O2"],
            "samples": [[4.0, 1.0, 3.0, 2.0], [8.0, 5.0, 7.0, 6.0]],
            "timestamps": [1_780_000_000.000, 1_780_000_000.004],
        }

        with mock.patch.object(adapter.time, "time", return_value=1_780_000_000.004):
            adapter._mirror_line_to_lsl(f"EEG_BATCH {json.dumps(payload)}")

        values, timestamps = outlet.chunks[0]
        self.assertEqual(values, [[4.0, 1.0, 3.0, 2.0], [8.0, 5.0, 7.0, 6.0]])
        self.assertAlmostEqual(timestamps[0], 499.996, places=6)
        self.assertAlmostEqual(timestamps[1], 500.0, places=6)

    def test_derived_batch_is_pushed_as_chunk_without_losing_backlog(self) -> None:
        class Outlet:
            def __init__(self) -> None:
                self.chunks = []

            def push_chunk(self, values, timestamps) -> None:
                self.chunks.append((values, timestamps))

        outlet = Outlet()
        adapter._lsl_outlets = {"BANDS": outlet}
        adapter._lsl_local_clock = lambda: 500.0
        payload = {
            "channels": ["delta", "theta", "alpha", "beta", "gamma"],
            "samples": [[0.1, 0.2, 0.3, 0.25, 0.15], [0.2, 0.2, 0.2, 0.2, 0.2]],
            "timestamps": [1_780_000_000.000, 1_780_000_000.040],
            "sample_count": 2,
        }

        with mock.patch.object(adapter.time, "time", return_value=1_780_000_000.040):
            adapter._mirror_line_to_lsl(f"BANDS_BATCH {json.dumps(payload)}")

        self.assertEqual(outlet.chunks[0][0], payload["samples"])
        self.assertAlmostEqual(outlet.chunks[0][1][-1], 500.0, places=6)


class HealthAndLoggingTests(unittest.TestCase):
    class RunningProcess:
        pid = 1

        def poll(self):
            return None

    def setUp(self) -> None:
        adapter._config = {
            "lsl_enabled": False,
            "disconnect_timeout_ms": 1000,
            "monitor_refresh_ms": 1000,
        }
        adapter._latest_state = {}
        adapter._process = self.RunningProcess()
        adapter._last_activity_at = 0.0
        adapter._last_any_line_at = 0.0
        adapter._last_sensor_activity_at = 0.0
        adapter._last_eeg_at = 0.0
        adapter._last_derived_at = 0.0
        adapter._signal_started_at = 0.0
        adapter._process_started_at = 0.0
        adapter._eeg_lsl_channels = ()
        adapter._lsl_stream_health = {}
        adapter._history_last_epoch_by_tag.clear()

    def tearDown(self) -> None:
        adapter._process = None
        adapter._latest_state = {}
        adapter._signal_started_at = 0.0
        adapter._eeg_lsl_channels = ()

    def test_log_output_does_not_count_as_raw_eeg(self) -> None:
        adapter._update_state_from_line("[WARN] diagnostic output only")

        health = adapter.get_status()["health"]

        self.assertEqual(health["log_output"], "receiving")
        self.assertEqual(health["raw_eeg"], "waiting")

    def test_log_noise_does_not_mask_missing_raw_after_signal_start(self) -> None:
        adapter._signal_started_at = 100.0
        adapter._last_any_line_at = 102.0
        adapter._latest_state = {"status": "warming_up", "signal_started_epoch": 100.0}

        adapter._check_connection_health_once(now=102.1)

        self.assertEqual(adapter._latest_state["status"], "stale")

    def test_battery_and_state_do_not_refresh_raw_data_watchdog(self) -> None:
        adapter._signal_started_at = 100.0
        adapter._last_eeg_at = 100.0
        adapter._latest_state = {
            "status": "connected",
            "signal_started_epoch": 100.0,
            "last_eeg_epoch": 100.0,
        }

        with mock.patch.object(adapter.time, "time", return_value=102.0):
            adapter._update_state_from_line('BATTERY {"percent":88}')
            adapter._update_state_from_line('STATE {"state":"InRange"}')

        self.assertEqual(adapter._last_eeg_at, 100.0)
        self.assertEqual(adapter._last_sensor_activity_at, 0.0)
        adapter._check_connection_health_once(now=102.1)
        self.assertEqual(adapter._latest_state["status"], "stale")

    def test_callback_error_is_visible_as_failure(self) -> None:
        adapter._update_state_from_line(
            'CALLBACK_ERROR {"phase":"signal","error_type":"AttributeError","error":"broken"}'
        )

        self.assertEqual(adapter._latest_state["status"], "failed")
        self.assertEqual(adapter._latest_state["callback_error"]["phase"], "signal")

    def test_eeg_batch_updates_latest_raw_sample(self) -> None:
        payload = {
            "channels": ["O1", "O2", "T3", "T4"],
            "samples": [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]],
            "timestamps": [100.0, 100.004],
            "packs": [10, 11],
            "sample_count": 2,
            "units": "uV",
            "processing": "unit_scale_only",
            "measured_hz": 248.5,
            "queue_overflow_dropped_total": 3,
        }

        adapter._update_state_from_line(f"EEG_BATCH {json.dumps(payload)}")

        self.assertEqual(adapter._latest_state["eeg"]["O1"], 5.0)
        self.assertEqual(adapter._latest_state["eeg_batch"]["last_pack"], 11)
        self.assertEqual(adapter._latest_state["eeg_batch"]["measured_hz"], 248.5)
        self.assertEqual(adapter._latest_state["eeg_batch"]["queue_overflow_dropped_total"], 3)
        self.assertEqual(adapter.get_status()["health"]["raw_eeg"], "receiving")

    def test_channel_map_drives_actual_raw_contract_without_derived_channels(self) -> None:
        payload = {
            "raw_channels": ["Fp1", "Fp2", "C3", "C4", "P3", "P4", "A1", "A2"],
            "fs_hz": 250,
            "derived_enabled": False,
        }

        adapter._update_state_from_line(f"CHANNEL_MAP {json.dumps(payload)}")

        eeg_stream = next(
            stream for stream in adapter._latest_state["actual_streams"] if stream["key"] == "eeg"
        )
        self.assertEqual(eeg_stream["channels"], payload["raw_channels"])
        self.assertEqual(adapter._latest_state["supported_channels"], payload["raw_channels"])
        self.assertFalse(adapter._latest_state["derived_enabled"])
        self.assertNotIn("bands", {stream["key"] for stream in adapter._latest_state["actual_streams"]})

    def test_derived_batch_updates_latest_and_one_hz_backup(self) -> None:
        adapter._history.clear()
        adapter._history_last_epoch_by_tag.clear()
        payload = {
            "channels": ["delta", "theta", "alpha", "beta", "gamma"],
            "samples": [[0.1, 0.2, 0.3, 0.25, 0.15], [0.2, 0.2, 0.2, 0.2, 0.2]],
            "timestamps": [100.0, 100.04],
            "sample_count": 2,
        }

        adapter._update_state_from_line(f"BANDS_BATCH {json.dumps(payload)}")

        self.assertEqual(adapter._latest_state["bands"]["alpha"], 0.2)
        self.assertEqual(adapter._latest_state["bands"]["ts"], 100.04)
        self.assertEqual(len(adapter._history), 1)
        self.assertEqual(adapter._history[0]["tag"], "BANDS")

    def test_noncanonical_raw_batch_is_healthy_when_it_matches_contract(self) -> None:
        adapter._eeg_lsl_channels = ("Fp1", "C3", "C4")
        adapter._latest_state = {"derived_enabled": False}
        payload = {
            "channels": ["Fp1", "C3", "C4"],
            "samples": [[1.0, 2.0, 3.0]],
            "timestamps": [100.0],
            "sample_count": 1,
        }

        adapter._update_state_from_line(f"EEG_BATCH {json.dumps(payload)}")

        self.assertEqual(adapter._latest_state["eeg"]["Fp1"], 1.0)
        self.assertEqual(adapter.get_status()["health"]["raw_eeg"], "receiving")
        self.assertEqual(adapter.get_status()["status"], "connected")
        self.assertEqual(adapter.get_status()["health"]["derived_metrics"], "not_applicable")

    def test_invalid_eeg_batch_does_not_refresh_raw_health(self) -> None:
        adapter._signal_started_at = 100.0
        adapter._last_eeg_at = 100.0
        adapter._latest_state = {
            "status": "connected",
            "signal_started_epoch": 100.0,
            "last_eeg_epoch": 100.0,
        }
        payload = {
            "channels": ["O1", "O2", "T3", "T4"],
            "samples": [[1.0, 2.0, 3.0, 4.0]],
            "timestamps": [102.0],
            "packs": [10, 11],
            "sample_count": 1,
        }

        with mock.patch.object(adapter.time, "time", return_value=102.0):
            adapter._update_state_from_line(f"EEG_BATCH {json.dumps(payload)}")

        self.assertEqual(adapter._last_eeg_at, 100.0)
        self.assertEqual(adapter._latest_state["status"], "failed")
        self.assertIn("packs length", adapter._latest_state["last_message"])

    def test_data_warning_is_visible_without_refreshing_raw_health(self) -> None:
        adapter._last_eeg_at = 100.0
        adapter._latest_state = {"last_eeg_epoch": 100.0}

        adapter._update_state_from_line(
            'DATA_WARNING {"phase":"signal_integrity","discarded_frames":1,"packet_gap_frames":2}'
        )

        status = adapter.get_status()
        self.assertEqual(adapter._last_eeg_at, 100.0)
        self.assertEqual(status["health"]["data_integrity"], "degraded")
        self.assertEqual(status["latest"]["data_warning_count"], 1)
        self.assertIn("2 packet-counter", status["last_message"])

    def test_lsl_push_failure_is_visible_in_recording_health(self) -> None:
        class BrokenOutlet:
            def push_chunk(self, values, timestamps):
                raise RuntimeError("outlet closed")

        adapter._config["lsl_enabled"] = True
        adapter._eeg_lsl_channels = ("O1", "O2", "T3", "T4")
        adapter._lsl_outlets = {"EEG": BrokenOutlet()}
        payload = {
            "channels": ["O1", "O2", "T3", "T4"],
            "samples": [[1.0, 2.0, 3.0, 4.0]],
            "timestamps": [100.0],
            "sample_count": 1,
        }

        adapter._push_eeg_chunk(payload)

        self.assertIn("outlet closed", adapter._latest_state["lsl_error"])
        self.assertEqual(adapter.get_status()["health"]["recording"], "failed")

    def test_log_rotation_keeps_numbered_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brainbit_runtime.log"
            path.write_text("0123456789", encoding="utf-8")

            rotated = adapter._rotate_log_file(path, max_bytes=5, backup_count=2)

            self.assertTrue(rotated)
            self.assertFalse(path.exists())
            self.assertEqual(path.with_name(f"{path.name}.1").read_text(encoding="utf-8"), "0123456789")

    def test_history_is_explicit_one_hz_projection_using_source_time(self) -> None:
        adapter._history.clear()
        adapter._history_last_epoch_by_tag.clear()

        adapter._append_history_projection("BANDS", {"ts": 100.0, "alpha": 0.1}, received_epoch=106.0, received_at="r1")
        adapter._append_history_projection("BANDS", {"ts": 100.04, "alpha": 0.2}, received_epoch=106.0, received_at="r2")
        adapter._append_history_projection("BANDS", {"ts": 101.0, "alpha": 0.3}, received_epoch=106.0, received_at="r3")

        self.assertEqual([sample["_epoch"] for sample in adapter._history], [100.0, 101.0])


class ConfigurationContractTests(unittest.TestCase):
    def test_resistance_quality_uses_megohm_scale(self) -> None:
        self.assertGreater(cli._resistance_to_quality(2_500), 0.99)
        self.assertEqual(cli._resistance_to_quality(cli.RESISTANCE_UPPER_OHM), 0.0)

    def test_driver_uses_generic_plugin_runtime(self) -> None:
        from study_runner.plugin_framework.driver_runtime import run_plugin_driver

        self.assertIs(driver.run_plugin_driver, run_plugin_driver)

    def test_backend_diagnostic_reports_raw_signal_statistics(self) -> None:
        summary = diagnose_backends.CaptureSummary("test", 30)
        summary.observe_batch(
            ["O1", "O2"],
            [[1.0, 0.0], [3.0, 0.0]],
            [100.0, 100.004],
        )

        report = summary.report(exit_code=0)

        self.assertEqual(report["sample_count"], 2)
        self.assertEqual(report["channel_statistics"]["O1"]["peak_to_peak"], 2.0)
        self.assertEqual(report["silent_or_constant_channels"], ["O2"])

    def test_backend_comparison_isolates_one_sided_raw_failure(self) -> None:
        comparison = diagnose_backends.compare_reports(
            {"backend": "neurosdk", "sample_count": 7500},
            {"backend": "brainflow", "sample_count": 0},
        )

        self.assertEqual(comparison["backends_with_raw_samples"], ["neurosdk"])
        self.assertIn("other backend", comparison["interpretation"])


if __name__ == "__main__":
    unittest.main()
