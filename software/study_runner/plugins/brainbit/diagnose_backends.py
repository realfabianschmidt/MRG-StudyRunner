"""Sequential 30-second BrainBit backend diagnostic.

This is deliberately outside the Study Runner acquisition path. It helps an
operator distinguish a headset/BLE problem from one backend implementation by
running NeuroSDK and BrainFlow separately and comparing their JSON reports.
Both backends must never be connected to the same band at the same time.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable


REPORT_TAG = "BRAINBIT_DIAGNOSTIC_REPORT"
COMPARISON_TAG = "BRAINBIT_DIAGNOSTIC_COMPARISON"
ERROR_TAGS = {
    "BLE_UNAVAILABLE",
    "CALLBACK_ERROR",
    "CONFIG_ERROR",
    "DEVICE_TARGET_MISSING",
    "EMO_INIT_FAIL",
    "STREAM_ERROR",
}


class CaptureSummary:
    def __init__(self, backend: str, duration_seconds: float) -> None:
        self.backend = backend
        self.duration_seconds = float(duration_seconds)
        self.channels: list[str] = []
        self.channel_stats: dict[str, dict[str, float | int | None]] = {}
        self.sample_count = 0
        self.invalid_sample_count = 0
        self.batch_count = 0
        self.first_timestamp: float | None = None
        self.last_timestamp: float | None = None
        self.max_timestamp_gap_seconds = 0.0
        self.non_monotonic_timestamps = 0
        self.packet_gap_frames_total = 0
        self.packet_counter_reset_total = 0
        self.derived_counts = {"bands": 0, "mental": 0}
        self.errors: list[dict[str, Any]] = []
        self.device: dict[str, Any] = {}
        self.channel_map: dict[str, Any] = {}
        self.nominal_rate_hz: float | None = None
        self.capture_started_monotonic: float | None = None
        self.capture_finished_monotonic: float | None = None

    def start_capture(self) -> None:
        if self.capture_started_monotonic is None:
            self.capture_started_monotonic = time.monotonic()

    def finish_capture(self) -> None:
        if self.capture_finished_monotonic is None:
            self.capture_finished_monotonic = time.monotonic()

    def observe_line(self, line: str) -> None:
        parsed = _parse_tagged_json(line)
        if parsed is None:
            return
        tag, payload = parsed
        if tag == "DEVICE":
            self.device = payload
            self.nominal_rate_hz = _finite_number(payload.get("fs_hz")) or self.nominal_rate_hz
        elif tag == "CHANNEL_MAP":
            self.channel_map = payload
            self.nominal_rate_hz = _finite_number(payload.get("fs_hz")) or self.nominal_rate_hz
        elif tag == "STREAM" and payload.get("stream") == "eeg":
            if payload.get("event") == "START":
                self.start_capture()
            elif payload.get("event") == "STOP":
                self.finish_capture()
        elif tag == "EEG_BATCH":
            self.observe_batch(
                payload.get("channels"),
                payload.get("samples"),
                payload.get("timestamps"),
            )
            self.packet_gap_frames_total = max(
                self.packet_gap_frames_total,
                int(payload.get("packet_gap_frames_total") or 0),
            )
            self.packet_counter_reset_total = max(
                self.packet_counter_reset_total,
                int(payload.get("packet_counter_reset_total") or 0),
            )
        elif tag in {"BANDS", "BANDS_BATCH"}:
            self.derived_counts["bands"] += int(payload.get("sample_count") or 1)
        elif tag in {"MENTAL", "MENTAL_BATCH"}:
            self.derived_counts["mental"] += int(payload.get("sample_count") or 1)
        elif tag in ERROR_TAGS:
            self.errors.append({"tag": tag, "payload": payload})

    def observe_batch(
        self,
        channels: Any,
        samples: Any,
        timestamps: Any,
    ) -> None:
        if not isinstance(channels, (list, tuple)) or not isinstance(samples, (list, tuple)):
            self.errors.append({"tag": "INVALID_BATCH", "payload": {"error": "missing channels/samples"}})
            return
        labels = [str(channel) for channel in channels]
        if not labels or len(labels) != len(set(labels)):
            self.errors.append({"tag": "INVALID_BATCH", "payload": {"error": "invalid channel labels"}})
            return
        if self.channels and labels != self.channels:
            self.errors.append(
                {"tag": "CHANNEL_CHANGE", "payload": {"previous": self.channels, "current": labels}}
            )
            return
        if not self.channels:
            self.channels = labels
            self.channel_stats = {
                label: {"finite_count": 0, "nonzero_count": 0, "minimum": None, "maximum": None, "sum_sq": 0.0}
                for label in labels
            }

        timestamp_values = list(timestamps) if isinstance(timestamps, (list, tuple)) else []
        if timestamp_values and len(timestamp_values) != len(samples):
            self.errors.append({"tag": "INVALID_BATCH", "payload": {"error": "timestamp length mismatch"}})
            timestamp_values = []

        self.batch_count += 1
        for index, sample in enumerate(samples):
            if not isinstance(sample, (list, tuple)) or len(sample) != len(labels):
                self.invalid_sample_count += 1
                continue
            numeric_sample: list[float] = []
            try:
                numeric_sample = [float(value) for value in sample]
            except (TypeError, ValueError):
                self.invalid_sample_count += 1
                continue
            if not all(math.isfinite(value) for value in numeric_sample):
                self.invalid_sample_count += 1
                continue

            self.sample_count += 1
            for label, value in zip(labels, numeric_sample, strict=True):
                stats = self.channel_stats[label]
                stats["finite_count"] = int(stats["finite_count"] or 0) + 1
                if abs(value) > 1e-12:
                    stats["nonzero_count"] = int(stats["nonzero_count"] or 0) + 1
                stats["minimum"] = value if stats["minimum"] is None else min(float(stats["minimum"]), value)
                stats["maximum"] = value if stats["maximum"] is None else max(float(stats["maximum"]), value)
                stats["sum_sq"] = float(stats["sum_sq"] or 0.0) + (value * value)

            if timestamp_values:
                timestamp = _finite_number(timestamp_values[index])
                if timestamp is None:
                    self.invalid_sample_count += 1
                    continue
                if self.last_timestamp is not None:
                    delta = timestamp - self.last_timestamp
                    if delta <= 0:
                        self.non_monotonic_timestamps += 1
                    else:
                        self.max_timestamp_gap_seconds = max(self.max_timestamp_gap_seconds, delta)
                if self.first_timestamp is None:
                    self.first_timestamp = timestamp
                self.last_timestamp = timestamp

    def report(self, *, exit_code: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        elapsed = None
        if self.capture_started_monotonic is not None and self.capture_finished_monotonic is not None:
            elapsed = max(0.0, self.capture_finished_monotonic - self.capture_started_monotonic)
        timestamp_rate = None
        if (
            self.sample_count > 1
            and self.first_timestamp is not None
            and self.last_timestamp is not None
            and self.last_timestamp > self.first_timestamp
        ):
            timestamp_rate = (self.sample_count - 1) / (self.last_timestamp - self.first_timestamp)
        effective_rate = (self.sample_count / elapsed) if elapsed and elapsed > 0 else None

        channels: dict[str, Any] = {}
        for label, values in self.channel_stats.items():
            count = int(values.get("finite_count") or 0)
            minimum = values.get("minimum")
            maximum = values.get("maximum")
            channels[label] = {
                "finite_count": count,
                "nonzero_count": int(values.get("nonzero_count") or 0),
                "minimum": minimum,
                "maximum": maximum,
                "peak_to_peak": (
                    float(maximum) - float(minimum)
                    if minimum is not None and maximum is not None
                    else None
                ),
                "rms": math.sqrt(float(values.get("sum_sq") or 0.0) / count) if count else None,
            }
        silent = [
            label
            for label, values in channels.items()
            if not values["nonzero_count"] or not values["peak_to_peak"]
        ]
        result = {
            "schema": "study-runner.brainbit-backend-diagnostic/v1",
            "backend": self.backend,
            "success": exit_code == 0 and self.sample_count > 0 and not self.errors,
            "exit_code": int(exit_code),
            "requested_duration_seconds": self.duration_seconds,
            "observed_capture_seconds": elapsed,
            "nominal_rate_hz": self.nominal_rate_hz,
            "effective_wall_rate_hz": effective_rate,
            "timestamp_rate_hz": timestamp_rate,
            "sample_count": self.sample_count,
            "invalid_sample_count": self.invalid_sample_count,
            "batch_count": self.batch_count,
            "channels": self.channels,
            "channel_statistics": channels,
            "silent_or_constant_channels": silent,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "max_timestamp_gap_seconds": self.max_timestamp_gap_seconds,
            "non_monotonic_timestamps": self.non_monotonic_timestamps,
            "packet_gap_frames_total": self.packet_gap_frames_total,
            "packet_counter_reset_total": self.packet_counter_reset_total,
            "derived_counts": dict(self.derived_counts),
            "device": self.device,
            "channel_map": self.channel_map,
            "errors": list(self.errors),
        }
        if extra:
            result.update(extra)
        return result


def _parse_tagged_json(line: str) -> tuple[str, dict[str, Any]] | None:
    parts = line.strip().split(" ", 1)
    if len(parts) != 2 or not parts[1].startswith("{"):
        return None
    try:
        payload = json.loads(parts[1])
    except json.JSONDecodeError:
        return None
    return (parts[0], payload) if isinstance(payload, dict) else None


def _finite_number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def build_neurosdk_command(args: argparse.Namespace) -> list[str]:
    cli_path = Path(__file__).with_name("brainbit_realtime_cli.py")
    command = [
        sys.executable,
        str(cli_path),
        "--scan-seconds",
        str(int(args.scan_seconds)),
        "--device-index",
        str(int(args.device_index)),
        "--no-resist",
        "--no-fpg",
        "--no-mems",
        "--signal-seconds",
        str(int(args.duration)),
        "--eeg-scale",
        "uV",
        "--no-osc",
        "--pretty",
        "--debug",
    ]
    for flag, value in (
        ("--serial-number", args.serial_number),
        ("--device-address", args.device_address),
        ("--device-name", args.device_name),
    ):
        if value:
            command.extend([flag, str(value)])
    return command


def run_neurosdk(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    collector = CaptureSummary("neurosdk", args.duration)
    command = build_neurosdk_command(args)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\r\n")
        collector.observe_line(line)
        if not args.quiet:
            print(f"[neurosdk] {line}", flush=True)
    exit_code = int(process.wait())
    collector.finish_capture()
    return collector.report(exit_code=exit_code, extra={"command": command}), exit_code


def run_brainflow(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    collector = CaptureSummary("brainflow", args.duration)
    try:
        from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams
    except ImportError as error:
        collector.errors.append(
            {
                "tag": "MISSING_DEPENDENCY",
                "payload": {
                    "error": str(error),
                    "install": f"{sys.executable} -m pip install brainflow",
                    "fallback": None,
                },
            }
        )
        return collector.report(exit_code=2), 2

    board_id = int(
        args.brainflow_board_id
        if args.brainflow_board_id is not None
        else BoardIds.BRAINBIT_BOARD.value
    )
    params = BrainFlowInputParams()
    params.serial_number = str(args.serial_number or "")
    params.timeout = int(args.brainflow_timeout)
    board = BoardShim(board_id, params)
    prepared = False
    streaming = False
    exit_code = 0
    extra: dict[str, Any] = {
        "board_id": board_id,
        "brainflow_params": {
            "serial_number": params.serial_number,
            "timeout": params.timeout,
        },
    }
    try:
        board.prepare_session()
        prepared = True
        sampling_rate = float(BoardShim.get_sampling_rate(board_id))
        collector.nominal_rate_hz = sampling_rate
        eeg_rows = list(BoardShim.get_eeg_channels(board_id))
        description = BoardShim.get_board_descr(board_id)
        names = [name.strip() for name in str(description.get("eeg_names") or "").split(",") if name.strip()]
        if len(names) != len(eeg_rows):
            names = [f"EEG_{index + 1}" for index in range(len(eeg_rows))]
        collector.device = {
            "name": description.get("name", "BrainBit via BrainFlow"),
            "serial_number": args.serial_number or None,
            "board_id": board_id,
        }
        collector.channel_map = {
            "raw_channels": names,
            "brainflow_eeg_rows": eeg_rows,
            "fs_hz": sampling_rate,
            "units": "uV",
        }
        board.start_stream()
        streaming = True
        collector.start_capture()
        deadline = time.monotonic() + float(args.duration)
        while time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        collector.finish_capture()
        data = board.get_board_data()
        sample_count = int(data.shape[1]) if len(data.shape) > 1 else 0
        samples = [
            [float(data[row][sample_index]) for row in eeg_rows]
            for sample_index in range(sample_count)
        ]
        timestamps: list[float] = []
        try:
            timestamp_row = int(BoardShim.get_timestamp_channel(board_id))
            timestamps = [float(data[timestamp_row][index]) for index in range(sample_count)]
        except Exception:
            if sample_count:
                end_epoch = time.time()
                timestamps = [
                    end_epoch - ((sample_count - 1 - index) / sampling_rate)
                    for index in range(sample_count)
                ]
        collector.observe_batch(names, samples, timestamps)
        try:
            package_row = int(BoardShim.get_package_num_channel(board_id))
            package_numbers = [int(data[package_row][index]) for index in range(sample_count)]
            gap_count, reset_count = packet_counter_diagnostics(package_numbers)
            collector.packet_gap_frames_total = gap_count
            collector.packet_counter_reset_total = reset_count
        except Exception:
            pass
    except Exception as error:
        exit_code = 8
        collector.errors.append(
            {"tag": "BRAINFLOW_ERROR", "payload": {"error_type": type(error).__name__, "error": str(error)}}
        )
    finally:
        if streaming:
            try:
                board.stop_stream()
            except Exception as error:
                collector.errors.append({"tag": "BRAINFLOW_STOP_ERROR", "payload": {"error": str(error)}})
                exit_code = exit_code or 8
        if prepared:
            try:
                board.release_session()
            except Exception as error:
                collector.errors.append({"tag": "BRAINFLOW_RELEASE_ERROR", "payload": {"error": str(error)}})
                exit_code = exit_code or 8
    return collector.report(exit_code=exit_code, extra=extra), exit_code


def packet_counter_diagnostics(values: Iterable[int]) -> tuple[int, int]:
    gaps = 0
    resets = 0
    previous: int | None = None
    for current in values:
        if previous is not None:
            direct = current - previous
            if direct > 1:
                gaps += direct - 1
            elif direct <= 0:
                wrapped = (256 - previous) + current if previous >= 230 and current <= 25 else None
                if wrapped is None:
                    resets += 1
                elif wrapped > 1:
                    gaps += wrapped - 1
        previous = current
    return gaps, resets


def compare_reports(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    reports = {str(report.get("backend") or f"backend_{index}"): report for index, report in enumerate((first, second))}
    active = [name for name, report in reports.items() if int(report.get("sample_count") or 0) > 0]
    if len(active) == 2:
        interpretation = (
            "Both independent backends received raw samples. A remaining Study Runner failure is likely "
            "in configuration, stream publication, or derived processing rather than the headset radio."
        )
    elif len(active) == 1:
        interpretation = (
            f"Only {active[0]} received raw samples. This strongly isolates the failure to the other "
            "backend or its device-selection/runtime setup; it does not prove electrode quality."
        )
    else:
        interpretation = (
            "Neither backend received raw samples. Check that runs were sequential, close the vendor app, "
            "verify Bluetooth/device selection and repeat before concluding that the headset is defective."
        )
    return {
        "schema": "study-runner.brainbit-backend-comparison/v1",
        "reports": reports,
        "backends_with_raw_samples": active,
        "interpretation": interpretation,
        "metrics": {
            name: {
                "success": report.get("success"),
                "sample_count": report.get("sample_count"),
                "effective_wall_rate_hz": report.get("effective_wall_rate_hz"),
                "packet_gap_frames_total": report.get("packet_gap_frames_total"),
                "silent_or_constant_channels": report.get("silent_or_constant_channels"),
                "errors": report.get("errors"),
            }
            for name, report in reports.items()
        },
    }


def _write_report(path: str, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"# Report written to {target}", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run sequential 30-second NeuroSDK/BrainFlow BrainBit diagnostics."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backend", choices=("neurosdk", "brainflow"))
    mode.add_argument("--compare", nargs=2, metavar=("REPORT_A", "REPORT_B"))
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--report", default="")
    parser.add_argument("--serial-number", default="")
    parser.add_argument("--device-address", default="")
    parser.add_argument("--device-name", default="")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--scan-seconds", type=int, default=10)
    parser.add_argument("--brainflow-board-id", type=int)
    parser.add_argument("--brainflow-timeout", type=int, default=15)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= float(args.duration) <= 600:
        raise SystemExit("--duration must be between 1 and 600 seconds")
    if args.compare:
        reports = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.compare]
        result = compare_reports(reports[0], reports[1])
        print(f"{COMPARISON_TAG} {json.dumps(result, ensure_ascii=False, separators=(',', ':'))}")
        if args.report:
            _write_report(args.report, result)
        return 0

    if args.backend == "neurosdk":
        report, exit_code = run_neurosdk(args)
    else:
        report, exit_code = run_brainflow(args)
    print(f"{REPORT_TAG} {json.dumps(report, ensure_ascii=False, separators=(',', ':'))}")
    if args.report:
        _write_report(args.report, report)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
