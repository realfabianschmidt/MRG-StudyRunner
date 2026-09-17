#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BrainBit NeuroSDK acquisition used by the Study Runner plugin.

This in-repository file is the shipped acquisition source of truth. It emits a
strict tagged-JSON protocol, preserves every discovered raw EEG channel, and
optionally derives EmotionalMath values when O1/O2/T3/T4 are all available.
The separate ``driver.py`` is only the generic plugin-process entry point.

Required packages are pinned in ``REQUIRED_MODULES`` below.
"""

from __future__ import annotations

import argparse
import builtins
import atexit
import importlib.util
from importlib.metadata import version, PackageNotFoundError
import json
import math
import os
import platform
import re
import signal as os_signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

REQUIRED_MODULES = [
    ("neurosdk", "pyneurosdk2==1.0.15"),
    ("pythonosc", "python-osc==1.9.3"),
    ("em_st_artifacts", "pyem-st-artifacts==1.0.8"),
]

# Exit codes the supervising adapter maps to plain-language operator messages.
# Keep these stable: adapter.py turns them into dashboard states.
EXIT_OK = 0
EXIT_MISSING_DEPENDENCY = 2
EXIT_NO_DEVICE_FOUND = 5
EXIT_DEVICE_TARGET_MISSING = 6
EXIT_CALLBACK_FAILURE = 7
EXIT_STREAM_FAILURE = 8
# The band was found but the connection attempt itself did not take. Retryable,
# and deliberately distinct from a crash: the host can say so plainly instead of
# reporting "stopped unexpectedly".
EXIT_CONNECT_FAILED = 9
EXIT_BLE_UNAVAILABLE = 103

# Nothing the retry loop can do about these: no Bluetooth adapter, or a package
# that is simply not installed. Everything else is worth another attempt.
FATAL_EXIT_CODES = frozenset({EXIT_MISSING_DEPENDENCY, EXIT_BLE_UNAVAILABLE})
# Matches the radar plugin's reconnect cadence. Flat, not escalating: a band
# that was switched on again should be picked up within seconds, every time.
RETRY_DELAY_SECONDS = 5.0

EEG_CHANNELS = ("O1", "O2", "T3", "T4")
RESISTANCE_UPPER_OHM = 2_666_000.0
EEG_STDOUT_INTERVAL_SECONDS = 0.1
# ~10 seconds of raw EEG at the sensor's native 250Hz. A stalled consumer (a
# blocked stdout pipe, a stuck host reader) must never grow this list without
# bound -- better to surface a visible, counted drop than to OOM silently.
EEG_PENDING_QUEUE_MAX_SAMPLES = 2500
_CONSOLE_LOCK = threading.RLock()


def _print(*args, **kwargs):
    """Keep tagged samples and callback diagnostics on separate complete lines."""
    with _CONSOLE_LOCK:
        builtins.print(*args, **kwargs)


def _is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _install_package(package_name: str) -> None:
    _print(f"[SETUP] Installing {package_name} ...", flush=True)
    subprocess.check_call([
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        package_name,
    ])


def _runtime_pip_install_disabled() -> bool:
    """Mirror of dependency_utils._runtime_pip_install_disabled.

    Duplicated on purpose: this file also runs as a standalone script, where the
    study_runner package is not importable.
    """
    if os.getenv("STUDY_RUNNER_DISABLE_RUNTIME_PIP", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    app_mode = os.getenv("STUDY_RUNNER_APP_MODE", "").strip().lower()
    if not app_mode:
        app_mode = "packaged" if getattr(sys, "frozen", False) else "python"
    return app_mode in {"desktop", "packaged"}


def _ensure_requirements() -> None:
    """Check the SDK dependencies, installing them only in source checkouts.

    Packaged builds bundle these libraries, so pip must never run there - it
    would need a compiler toolchain the operator's machine does not have.
    """
    missing = [
        (module_name, package_name)
        for module_name, package_name in REQUIRED_MODULES if module_name != "em_st_artifacts"
        if not _is_module_available(module_name)
    ]
    if not missing:
        _print(f"[SETUP] Required libraries are installed: {', '.join(m for m, _ in REQUIRED_MODULES)}", flush=True)
        return

    names = ", ".join(package_name for _, package_name in missing)
    _print(f"[SETUP] Missing required libraries: {names}", flush=True)

    if _runtime_pip_install_disabled():
        _print_json("SETUP_FAIL", {"missing": [p for _, p in missing], "auto_install": False})
        raise SystemExit(EXIT_MISSING_DEPENDENCY)

    for _, package_name in missing:
        try:
            _install_package(package_name)
        except Exception as exc:
            _print(f"[ERROR] Could not install {package_name}: {exc}", flush=True)
            _print_json("SETUP_FAIL", {"missing": [p for _, p in missing], "error": str(exc)})
            raise SystemExit(EXIT_MISSING_DEPENDENCY)

    still_missing = [package_name for module_name, package_name in missing if not _is_module_available(module_name)]
    if still_missing:
        _print(f"[ERROR] Missing required module after installation: {', '.join(still_missing)}", flush=True)
        _print_json("SETUP_FAIL", {"missing": still_missing, "after_install": True})
        raise SystemExit(EXIT_MISSING_DEPENDENCY)

    _print(f"[SETUP] Required libraries are installed: {', '.join(m for m, _ in REQUIRED_MODULES)}", flush=True)


def _load_sdk_modules() -> None:
    """Import the vendor SDKs into module globals.

    Deferred until after _ensure_requirements so that a missing dependency
    produces a tagged, machine-readable line instead of an import traceback.
    """
    global Scanner, SensorFamily, SensorFeature, SensorCommand, SensorGain
    global BrainBit2ChannelMode, GenCurrent, SimpleUDPClient
    global lib_settings, support_classes, emotional_math

    from neurosdk.scanner import Scanner
    from neurosdk.cmn_types import (
        SensorFamily,
        SensorFeature,
        SensorCommand,
        SensorGain,
        BrainBit2ChannelMode,
        GenCurrent,
    )
    from pythonosc.udp_client import SimpleUDPClient
    try:
        from em_st_artifacts.utils import lib_settings, support_classes
        from em_st_artifacts import emotional_math
    except Exception as error:
        lib_settings = support_classes = emotional_math = None
        _print_json("EMO_INIT_FAIL", {"error": str(error), "raw_stream_continues": True})


def _validate_sdk_api_surface() -> None:
    """Report optional math API mismatches before scanning; raw EEG can continue."""
    if not hasattr(getattr(emotional_math, "EmotionalMath", None), "push_bipolars"):
        message = (
            "Pinned pyem-st-artifacts wheel does not expose "
            "EmotionalMath.push_bipolars; derived metrics are unavailable."
        )
        _print(f"[ERROR] {message}", flush=True)
        _print_json("EMO_INIT_FAIL", {"missing_api": "EmotionalMath.push_bipolars", "message": message,
                                      "raw_stream_continues": True})


# ----------------- small utils -----------------
def _enum_name(x: Any) -> str:
    try: return x.name
    except Exception: return str(x)

def _safe(obj: Any, name: str, default=None):
    try: return getattr(obj, name)
    except Exception: return default

def _iter(payload: Any) -> Iterable:
    if payload is None: return []
    return payload if isinstance(payload, (list, tuple)) else [payload]


def _json_number(value: Any) -> float | int | None:
    """Return a standards-compliant JSON number or None for open/invalid values."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    if isinstance(value, int):
        return int(value)
    return numeric


def _brainbit_sensor_families(sensor_family: Any) -> list[Any]:
    """Build the scanner filter without assuming every SDK exposes every model."""
    names = (
        "LEBrainBit",
        "LEBrainBitBlack",
        "LEBrainBit2",
        "LEBrainBitPro",
        "LEBrainBitFlex",
    )
    families = []
    for name in names:
        family = getattr(sensor_family, name, None)
        if family is not None and family not in families:
            families.append(family)
    return families


def _channel_label(channel_info: Any) -> str:
    """Normalize an EEGChannelInfo object to its physical channel label."""
    candidates = (
        _safe(channel_info, "Name"),
        _safe(_safe(channel_info, "Id"), "name"),
        _safe(channel_info, "Id"),
    )
    for candidate in candidates:
        compact = re.sub(r"[^A-Z0-9]", "", str(candidate or "").upper())
        compact = compact.removeprefix("EEGCHID")
        if compact:
            for channel in EEG_CHANNELS:
                if compact == channel or compact.endswith(channel):
                    return channel
    name = str(_safe(channel_info, "Name") or "").strip()
    if name:
        return name
    number = _safe(channel_info, "Num")
    return f"CH{number}" if number is not None else "UNKNOWN"


def _supported_channel_index_map(supported_channels: Iterable[Any]) -> dict[str, int]:
    """Map SDK channel labels to their documented Samples-array positions."""
    mapping: dict[str, int] = {}
    used_indices: set[int] = set()
    for position, info in enumerate(supported_channels or []):
        raw_index = _safe(info, "Num", position)
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid supported channel index {raw_index!r}") from exc
        label = _channel_label(info)
        if index < 0:
            raise ValueError(f"Negative supported channel index for {label}: {index}")
        if label in mapping:
            raise ValueError(f"Duplicate supported channel label: {label}")
        if index in used_indices:
            raise ValueError(f"Duplicate supported channel index: {index}")
        mapping[label] = index
        used_indices.add(index)
    return mapping


def _ordered_channel_labels(channel_index_map: dict[str, int]) -> list[str]:
    """Return the device's sample labels in the exact SDK Samples-array order."""
    return [
        label
        for label, _ in sorted(channel_index_map.items(), key=lambda item: item[1])
    ]


def _decode_packet_channels(
    packet: Any,
    channel_index_map: dict[str, int] | None = None,
    *,
    require_finite: bool = True,
) -> tuple[dict[str, float], str]:
    """Decode either classic O1/O2/T3/T4 fields or BrainBit2 Samples arrays."""
    classic = {channel: _safe(packet, channel) for channel in EEG_CHANNELS}
    if all(value is not None for value in classic.values()):
        source = "classic_fields"
        values = classic
    else:
        samples = _safe(packet, "Samples")
        if samples is None:
            raise ValueError("packet has neither classic EEG fields nor a Samples array")
        if not channel_index_map:
            raise ValueError("array packet received without supported_channels mapping")
        samples = list(samples)
        values = {}
        for channel in _ordered_channel_labels(channel_index_map):
            index = channel_index_map[channel]
            if index >= len(samples):
                raise ValueError(
                    f"supported channel {channel} points to Samples[{index}], "
                    f"but packet has {len(samples)} values"
                )
            values[channel] = samples[index]
        source = "samples_array"

    decoded: dict[str, float] = {}
    for channel, value in values.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"non-numeric {channel} value: {value!r}") from exc
        if require_finite and not math.isfinite(numeric):
            raise ValueError(f"non-finite {channel} value: {numeric!r}")
        decoded[channel] = numeric
    return decoded, source


def _resistance_to_quality(
    resistance_ohm: Any,
    max_ohm: float = RESISTANCE_UPPER_OHM,
) -> float:
    value = _json_number(resistance_ohm)
    if value is None or value <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (float(value) / float(max_ohm))))


def _percent_to_ratio(value: Any) -> float | None:
    """Convert EmotionalMath's documented 0..100 percent outputs to 0..1."""
    numeric = _json_number(value)
    if numeric is None:
        return None
    return max(0.0, min(1.0, float(numeric) / 100.0))


def _result_value(result: Any, *names: str) -> Any:
    """Read current lowercase SDK fields while retaining older-wheel compatibility."""
    for name in names:
        if isinstance(result, dict) and name in result:
            return result[name]
        try:
            return getattr(result, name)
        except (AttributeError, TypeError):
            continue
    return None


def _measured_hz(total_samples: int, elapsed_seconds: float) -> float | None:
    """Average measured EEG sample rate since the stream started.

    Same method as diagnose_backends.py's effective_wall_rate_hz: total
    samples divided by wall-clock elapsed time, not the nominal 250Hz. None
    while too little time has elapsed to be meaningful.
    """
    if elapsed_seconds <= 0 or total_samples <= 0:
        return None
    return total_samples / elapsed_seconds


def _apply_eeg_queue_cap(
    pending: list[Any],
    max_samples: int = EEG_PENDING_QUEUE_MAX_SAMPLES,
) -> int:
    """Drop the oldest samples in-place once ``pending`` exceeds ``max_samples``.

    A stalled consumer (a blocked stdout pipe, a stuck host reader) must never
    let this queue grow without bound. Returns how many samples were dropped
    so the caller can report a visible, counted QC event instead of an
    operator finding out only when the process runs out of memory.
    """
    excess = len(pending) - max_samples
    if excess <= 0:
        return 0
    del pending[:excess]
    return excess


def _push_bipolar_samples(math_lib: Any, samples: list[Any]) -> None:
    push = getattr(math_lib, "push_bipolars", None)
    if not callable(push):
        raise AttributeError(
            "The pinned EmotionalMath API must expose push_bipolars; "
            "refusing an ambiguous legacy push_data fallback"
        )
    push(samples)


def _configure_emotional_math(math_lib: Any, *, calibration_sec: int, skip_windows: int) -> None:
    math_lib.set_calibration_length(int(calibration_sec))
    math_lib.set_mental_estimation_mode(False)
    math_lib.set_skip_wins_after_artifact(int(skip_windows))
    if hasattr(math_lib, "set_squared_spectrum"):
        math_lib.set_squared_spectrum(True)
    # Native argument semantics are inverted relative to the method name: one
    # retains a band. The public stream declares all five, so retain all five.
    math_lib.set_zero_spect_waves(True, 1, 1, 1, 1, 1)
    math_lib.set_spect_normalization_by_bands_width(True)


class SourceTimestampEstimator:
    """Reconstruct sample times and retain observable SDK packet-counter gaps."""

    def __init__(self, sampling_rate_hz: float) -> None:
        self.sample_interval = 1.0 / float(sampling_rate_hz)
        self.last_timestamp: float | None = None
        self.last_packet_number: int | None = None
        self.packet_gap_frames_total = 0
        self.packet_counter_reset_total = 0

    def for_batch(self, sample_count: int, received_epoch: float) -> list[float]:
        if sample_count <= 0:
            return []
        first = float(received_epoch) - ((sample_count - 1) * self.sample_interval)
        if self.last_timestamp is not None and first <= self.last_timestamp:
            first = self.last_timestamp + self.sample_interval
        timestamps = [first + (index * self.sample_interval) for index in range(sample_count)]
        self.last_timestamp = timestamps[-1]
        return timestamps

    def for_packets(
        self,
        packet_numbers: Iterable[Any],
        received_epoch: float,
    ) -> tuple[list[float], list[dict[str, Any]]]:
        """Timestamp frames using packet advances when the SDK exposes them.

        NeuroSDK provides only a host callback time, not a device timestamp for
        every frame. ``PackNum`` is therefore used solely to preserve observable
        holes in the nominal timeline. Counter resets and absent/non-integral
        counters fall back to one nominal interval and are reported explicitly.
        """
        parsed = [self._packet_number(value) for value in packet_numbers]
        if not parsed:
            return [], []

        previous = self.last_packet_number
        steps: list[int] = []
        events: list[dict[str, Any]] = []
        for current in parsed:
            event: dict[str, Any] = {"gap_before": 0}
            if previous is None or current is None:
                advance = 1
                if current is None:
                    event["counter_event"] = "unknown"
            else:
                advance, counter_event = self._packet_advance(previous, current)
                if counter_event == "gap":
                    missing = advance - 1
                    event.update(
                        {
                            "gap_before": missing,
                            "counter_event": "gap",
                            "previous_pack": previous,
                            "current_pack": current,
                        }
                    )
                    self.packet_gap_frames_total += missing
                elif counter_event:
                    event.update(
                        {
                            "counter_event": counter_event,
                            "previous_pack": previous,
                            "current_pack": current,
                        }
                    )
                    if counter_event == "reset":
                        self.packet_counter_reset_total += 1
            steps.append(advance)
            events.append(event)
            # Do not bridge an unknown counter: doing so could count a valid
            # frame with missing metadata as both present and dropped.
            previous = current

        within_batch_steps = sum(steps[1:])
        first = float(received_epoch) - (within_batch_steps * self.sample_interval)
        if self.last_timestamp is not None:
            first = self.last_timestamp + (steps[0] * self.sample_interval)

        timestamps = [first]
        for advance in steps[1:]:
            timestamps.append(timestamps[-1] + (advance * self.sample_interval))

        self.last_timestamp = timestamps[-1]
        self.last_packet_number = parsed[-1]
        return timestamps, events

    @staticmethod
    def _packet_number(value: Any) -> int | None:
        numeric = _json_number(value)
        if numeric is None:
            return None
        integer = int(numeric)
        if float(numeric) != float(integer) or integer < 0:
            return None
        return integer

    @staticmethod
    def _packet_advance(previous: int, current: int) -> tuple[int, str | None]:
        direct = current - previous
        if direct == 1:
            return 1, None
        if direct > 1 and direct <= 10_000:
            return direct, "gap"
        if direct == 0:
            return 1, "duplicate"

        # SDK generations have used counters of different widths. Only treat a
        # decrease as a wrap when both values are close to a common boundary;
        # otherwise it is a reset/reconnect and no giant artificial gap is made.
        for modulus in (256, 65_536, 4_294_967_296):
            if previous < modulus and previous >= int(modulus * 0.9) and current <= int(modulus * 0.1):
                wrapped = (modulus - previous) + current
                if wrapped == 1:
                    return 1, "wrap"
                if 1 < wrapped <= 10_000:
                    return wrapped, "gap"
        return 1, "reset"

DEBUG = False
PRETTY = False
_LAST_OSC_ERROR_AT = 0.0
_OUTPUT_LOCK = threading.Lock()

# The one live sensor handle, so the interpreter can always let go of the band
# on its way out. A BrainBit that is never disconnected stays held at the BLE
# layer and refuses the next connection attempt, so releasing it is worth a
# best-effort attempt even on paths that skipped the normal `finally`.
_ACTIVE_SENSOR: Any = None
_ACTIVE_SENSOR_LOCK = threading.Lock()


def _remember_active_sensor(sensor: Any) -> None:
    global _ACTIVE_SENSOR
    with _ACTIVE_SENSOR_LOCK:
        _ACTIVE_SENSOR = sensor


def _release_active_sensor() -> None:
    """Disconnect the band if nothing else has. Safe to call more than once."""
    global _ACTIVE_SENSOR
    with _ACTIVE_SENSOR_LOCK:
        sensor, _ACTIVE_SENSOR = _ACTIVE_SENSOR, None
    if sensor is None:
        return
    for attribute in ("signalDataReceived", "resistDataReceived", "fpgDataReceived", "memsDataReceived"):
        try:
            setattr(sensor, attribute, None)
        except Exception:
            pass
    try:
        sensor.disconnect()
    except Exception:
        pass


atexit.register(_release_active_sensor)


def _print_json(tag: str, data: dict):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with _OUTPUT_LOCK:
        _print(f"{tag} {payload}", flush=True)


def _set_output_mode(debug: bool, pretty: bool) -> None:
    global DEBUG, PRETTY
    DEBUG = debug
    PRETTY = pretty
    _status(f"Pretty output={PRETTY}, Debug mode={DEBUG}")


def _status(*args, **kwargs):
    if PRETTY or DEBUG:
        _print("[STATUS]", *args, **kwargs, flush=True)


def _debug(*args, **kwargs):
    if DEBUG:
        _print("[DEBUG]", *args, **kwargs, flush=True)


def _warn(*args, **kwargs):
    _print("[WARN]", *args, **kwargs, flush=True)


def _pretty_line(label: str, line: str) -> None:
    if PRETTY:
        _print(f"[{label}] {line}", flush=True)


def _print_sensor_summary(sensor) -> None:
    family = _enum_name(_safe(sensor, "sens_family"))
    features = _safe(sensor, "features")
    commands = _safe(sensor, "commands")
    sensor_info = {
        "family": family,
        "name": _safe(sensor, "name"),
        "address": _safe(sensor, "address"),
        "serial": _safe(sensor, "serial_number"),
        "sampling_frequency": str(_safe(sensor, "sampling_frequency")),
        "battery": _safe(sensor, "batt_power"),
        "firmware": str(_safe(sensor, "version", "unknown")),
        "features": [f.name for f in features] if isinstance(features, (list, tuple)) else str(features),
        "commands": [c.name for c in commands] if isinstance(commands, (list, tuple)) else str(commands),
    }
    for package in ("pyneurosdk2", "pyem-st-artifacts"):
        try:
            sensor_info[package] = version(package)
        except PackageNotFoundError:
            sensor_info[package] = "unknown"
    _print_json("DEVICE", sensor_info)
    if sensor_info["battery"] is not None:
        _print_json("BATTERY", {"percent": sensor_info["battery"]})
    _status("Connected sensor info:")
    for key, value in sensor_info.items():
        _status(f"  {key}: {value}")


def _sensor_info_payload(info, index: int) -> dict:
    return {
        "index": index,
        "name": _safe(info, "Name"),
        "family": _enum_name(_safe(info, "SensFamily")),
        "address": _safe(info, "Address"),
        "serial": _safe(info, "SerialNumber"),
        "pairing_required": _safe(info, "PairingRequired"),
        "rssi": _safe(info, "RSSI"),
    }


def _wall_clock_in(seconds: float) -> str:
    """A human-readable clock time `seconds` from now, for status messages."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + seconds))


def _normalize_target(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_address(value: Any) -> str:
    """Compare BLE addresses on their hex digits alone.

    The SDK, the Windows Bluetooth page and the settings file disagree about
    separators (``AA:BB:CC``, ``AA-BB-CC``, bare hex) and about case. Comparing
    the raw strings meant a correctly typed address silently failed to match,
    and selection then fell through to the *next* selector -- in practice to a
    different band than the one the operator had configured.
    """
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


# Everything the scan has seen this session, in first-seen order. One list, so
# the index printed on a SCAN line (what the dashboard's "Use this band" button
# sends back) means the same position the selector will later use.
_SCAN_SEEN: List[Any] = []
_SCAN_SEEN_KEYS: set = set()
_SCAN_LOCK = threading.Lock()


def _candidate_identity(info: Any) -> tuple:
    return (
        _normalize_target(_safe(info, "SerialNumber")),
        _normalize_address(_safe(info, "Address")),
        _normalize_target(_safe(info, "Name")),
    )


def _reset_scan_candidates() -> None:
    """Forget the previous scan. Called once per connection attempt."""
    with _SCAN_LOCK:
        _SCAN_SEEN.clear()
        _SCAN_SEEN_KEYS.clear()


def _remember_scan_candidates(sensors: Iterable[Any]) -> List[Tuple[int, Any]]:
    """Add bands we have not seen before; return them with their stable index."""
    added: List[Tuple[int, Any]] = []
    with _SCAN_LOCK:
        for info in sensors or []:
            identity = _candidate_identity(info)
            if identity in _SCAN_SEEN_KEYS:
                continue
            _SCAN_SEEN_KEYS.add(identity)
            _SCAN_SEEN.append(info)
            added.append((len(_SCAN_SEEN) - 1, info))
    return added


def _scan_candidates() -> List[Any]:
    with _SCAN_LOCK:
        return list(_SCAN_SEEN)


def _select_sensor_info(sensors: List[Any], args) -> Tuple[Optional[int], Optional[Any], str]:
    if getattr(args, "require_selection", False):
        return None, None, "selection_required"
    serial_target = _normalize_target(args.serial_number)
    address_target = _normalize_address(args.device_address)
    name_target = _normalize_target(args.device_name)
    if serial_target:
        for idx, info in enumerate(sensors):
            if _normalize_target(_safe(info, "SerialNumber")) == serial_target:
                return idx, info, "serial_number"
        return None, None, f"serial_number '{args.serial_number}' not found"
    if address_target:
        for idx, info in enumerate(sensors):
            if _normalize_address(_safe(info, "Address")) == address_target:
                return idx, info, "device_address"
        return None, None, f"device_address '{args.device_address}' not found"
    if name_target:
        return None, None, "selection_required"
    if len(sensors) == 1 and (_safe(sensors[0], "SerialNumber") or _safe(sensors[0], "Address")):
        return 0, sensors[0], "single_device"
    return None, None, "selection_required"


def _format_values(**kwargs) -> str:
    return ", ".join(f"{k}={v}" for k, v in kwargs.items())


def _send_num(osc: SimpleUDPClient, label: str, name: str, val):
    if osc is None or val is None: return
    try:
        v = float(val)
        if math.isnan(v): return
        osc.send_message(f"/BrainBit/{label}/{name}", v)
    except Exception as error:
        _report_osc_error(f"{label}/{name}", error)

def _send_root(osc: SimpleUDPClient, name: str, val):
    if osc is None or val is None: return
    try:
        v = float(val)
        if math.isnan(v): return
        osc.send_message(f"/BrainBit/{name}", v)
    except Exception as error:
        _report_osc_error(name, error)


def _report_osc_error(path: str, error: Exception) -> None:
    global _LAST_OSC_ERROR_AT
    now = time.monotonic()
    if now - _LAST_OSC_ERROR_AT >= 5.0:
        _LAST_OSC_ERROR_AT = now
        _warn(f"OSC send failed for {path}: {error}")

def _get_sampling_rate_hz(sensor, default_hz: int = 250) -> int:
    sf = _safe(sensor, "sampling_frequency")
    for src in (_safe(sf, "name"), str(sf), repr(sf)):
        if src:
            m = re.search(r"(\d{2,4})", src)
            if m:
                hz = int(m.group(1))
                if 50 <= hz <= 2000: return hz
    val = _safe(sf, "value")
    if isinstance(val, int) and 50 <= val <= 2000: return val
    return default_hz

# --- simple streaming filters for console/OSC only ---
class DCDetrender:
    def __init__(self, alpha: float = 0.01):
        self.alpha = float(alpha); self.mu: Optional[float] = None
    def step(self, x: float) -> float:
        if self.mu is None: self.mu = float(x)
        self.mu = (1.0 - self.alpha) * self.mu + self.alpha * float(x)
        return float(x) - self.mu

class NotchIIR:
    def __init__(self, fs: float, f0: float, Q: float = 30.0):
        self.fs, self.f0, self.Q = float(fs), float(f0), float(Q)
        self._z1 = self._z2 = 0.0; self._y1 = self._y2 = 0.0
        self._compute_coefs()
    def _compute_coefs(self):
        import math as _m
        w0 = 2.0 * _m.pi * (self.f0 / self.fs)
        alpha = _m.sin(w0) / (2.0 * self.Q); cosw0 = _m.cos(w0)
        b0 = 1.0; b1 = -2.0 * cosw0; b2 = 1.0
        a0 = 1.0 + alpha; a1 = -2.0 * cosw0; a2 = 1.0 - alpha
        self.b0, self.b1, self.b2 = b0/a0, b1/a0, b2/a0
        self.a1, self.a2 = a1/a0, a2/a0
    def step(self, x: float) -> float:
        y = self.b0*x + self.b1*self._z1 + self.b2*self._z2 - self.a1*self._y1 - self.a2*self._y2
        self._z2, self._z1 = self._z1, x
        self._y2, self._y1 = self._y1, y
        return y

# BLE preflight with macOS tips (Code 103)
def _start_scan_or_explain(scanner) -> None:
    """Start discovery, or explain plainly that Bluetooth is unusable.

    Only starts it. How long to listen and when to stop belongs to
    `_scan_for_target`, which can end the scan the moment the right band
    answers instead of always waiting out a fixed window.
    """
    try:
        scanner.start()
    except Exception as e:
        msg = str(e)
        if "Code 103" in msg or "BLE adapter not found or disabled" in msg:
            _print("# FATAL: BLE adapter not found or disabled.", flush=True)
            _print_json("BLE_UNAVAILABLE", {"message": "Bluetooth adapter not found or disabled."})
            if platform.system() == "Darwin":
                _print("# macOS checklist:\n#  1) Bluetooth ON.\n#  2) Privacy → Bluetooth: allow your terminal.\n#  3) Headband not connected elsewhere.\n#  4) If stuck: sudo killall -9 bluetoothd; toggle BT.\n#  5) which python3", flush=True)
            raise SystemExit(EXIT_BLE_UNAVAILABLE)
        else:
            raise


def _scan_for_target(scanner, args, stop_event: threading.Event) -> List[Any]:
    """Scan until the configured band is resolvable, then stop immediately.

    Replaces a flat `sleep(scan_seconds)`. Three things went wrong with that:

    * It always waited the full window even once the right band had answered,
      so every start paid the worst case.
    * It never waited *longer* than the window, so a band that advertises
      slowly -- which BLE devices legitimately do, especially just after being
      switched on -- was simply missed.
    * Selection then indexed `scanner.sensors()` taken after `stop()`, while
      the dashboard's candidate list came from the `sensorsChanged` callback.
      Two lists, two orderings, one shared index: the band an operator clicked
      was not reliably the band that got connected.

    All three are the same fix: accumulate candidates as they arrive, check
    after every slice whether the target resolves, and select from exactly the
    set that was accumulated.
    """
    window = max(1.0, float(args.scan_seconds))
    # Only a band named by serial, address or name can be recognised early. With
    # none of those configured the selection is positional, so cutting the scan
    # short would hide the other bands the operator still has to choose between.
    has_named_target = bool(
        _normalize_target(args.serial_number)
        or _normalize_address(args.device_address)
    )
    # A named band is worth waiting three windows for, because BLE devices
    # legitimately take seconds to start advertising -- especially one that was
    # just switched on. Without a name there is nothing to wait *for*, so the
    # nominal window is the whole discovery pass.
    deadline = window * 3.0 if has_named_target else window

    started = time.monotonic()
    _start_scan_or_explain(scanner)
    try:
        while not stop_event.is_set():
            candidates = _accumulated_candidates(scanner)
            if has_named_target:
                index, info, _ = _select_sensor_info(candidates, args)
                if info is not None and index is not None:
                    return candidates
            if (time.monotonic() - started) >= deadline:
                break
            stop_event.wait(0.25)
        return _accumulated_candidates(scanner)
    finally:
        try:
            scanner.stop()
        except Exception:
            pass


def _accumulated_candidates(scanner) -> List[Any]:
    """Every band seen so far, in stable first-seen order.

    The ``sensorsChanged`` callback and ``scanner.sensors()`` do not always
    agree, so both feed the same list; anything the poll finds that the callback
    missed is announced with a SCAN line too, so the dashboard and the selector
    never disagree about which band index 2 is.
    """
    for index, info in _remember_scan_candidates(_safe_sensor_list(scanner)):
        _print_json("SCAN", _sensor_info_payload(info, index))
    return _scan_candidates()


def _safe_sensor_list(scanner) -> List[Any]:
    try:
        return list(scanner.sensors() or [])
    except Exception:
        return []


# ----------------- main -----------------
def main(argv: Optional[List[str]] = None):
    ap = argparse.ArgumentParser(description="BrainBit CLI + OSC + Emotions (Bands + Mind) with calibration watchdog")
    ap.add_argument("--scan-seconds", type=int, default=5)
    ap.add_argument("--device-index", type=int, default=0)
    ap.add_argument("--device-address", type=str, default="")
    ap.add_argument("--serial-number", type=str, default="")
    ap.add_argument("--device-name", type=str, default="")
    ap.add_argument("--require-selection", action="store_true")
    ap.add_argument(
        "--max-session-attempts",
        type=int,
        default=0,
        help="0 = keep reconnecting until stopped. Only tests set a limit.",
    )

    # staging (per SDK: Resist and Signal cannot run simultaneously)
    ap.add_argument("--no-resist", action="store_true")
    ap.add_argument("--resist-seconds", type=int, default=6)
    ap.add_argument("--no-fpg", action="store_true")
    ap.add_argument("--fpg-seconds", type=int, default=0)
    ap.add_argument("--no-mems", action="store_true")
    ap.add_argument("--mems-seconds", type=int, default=0)
    ap.add_argument("--signal-seconds", type=int, default=0, help="0 = until Ctrl+C")

    # print/OSC smoothing (does NOT affect Emotions lib input)
    ap.add_argument("--detrend-alpha", type=float, default=0.01, help="EMA baseline alpha (0..1).")
    ap.add_argument("--mains-hz", type=int, default=50, choices=[0, 50, 60], help="Notch mains (0=off).")

    # EEG scaling/precision
    ap.add_argument("--eeg-scale", type=str, default="uV", choices=["V", "mV", "uV"])
    ap.add_argument("--eeg-precision", type=int, default=3)

    # Emotions settings (match official sample)
    ap.add_argument("--process-win-freq", type=int, default=25)
    ap.add_argument("--fft-window-samples", type=int, default=1000)  # sample uses 1000 for fs=250
    ap.add_argument("--skip-first-sec", type=int, default=4)
    ap.add_argument("--calibration-sec", type=int, default=6)
    ap.add_argument("--nwins-skip-after-artifact", type=int, default=10)

    # Calibration watchdog
    ap.add_argument("--calib-max-sec", type=float, default=20.0)
    ap.add_argument("--calib-stall-sec", type=float, default=8.0)
    ap.add_argument("--force-on-artifacts", action="store_true")
    ap.add_argument("--art-streak-sec", type=float, default=10.0)

    # OSC
    ap.add_argument("--no-osc", action="store_true")
    ap.add_argument("--osc-host", type=str, default="127.0.0.1")
    ap.add_argument("--osc-port", type=int, default=8000)

    ap.add_argument("--pretty", action="store_true", help="Show readable terminal status output in addition to JSON.")
    ap.add_argument("--debug", action="store_true", help="Print debug event nodes and data flow details.")

    args = ap.parse_args(argv)
    _set_output_mode(debug=args.debug, pretty=args.pretty)
    _ensure_requirements()
    _load_sdk_modules()
    _validate_sdk_api_surface()
    osc = None if args.no_osc else SimpleUDPClient(args.osc_host, int(args.osc_port))

    stop_event = threading.Event()

    def _on_stop_signal(signum, frame):
        _print(f"\n# Stop signal {signum} — stopping streams ...", flush=True)
        stop_event.set()

    # Every signal the host can actually send has to reach the same handler,
    # or the `finally: sensor.disconnect()` at the end of streaming never runs
    # and the band stays held at the BLE layer -- which looks to an operator
    # like "it connects, then immediately drops", curable only by repeated
    # start/stop until the stack lets go by itself.
    #
    # The host sends CTRL_BREAK_EVENT on Windows (adapter.stop(), because the
    # child is spawned with CREATE_NEW_PROCESS_GROUP and CTRL_C_EVENT cannot
    # be delivered to a specific group member). That arrives as SIGBREAK, not
    # SIGINT, and Python's default SIGBREAK handler terminates the process
    # outright. SIGINT alone was therefore never enough on the platform this
    # actually runs on.
    for signal_name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        handled_signal = getattr(os_signal, signal_name, None)
        if handled_signal is None:
            continue
        try:
            os_signal.signal(handled_signal, _on_stop_signal)
        except (ValueError, OSError):
            # Not every signal is settable on every platform/thread; a signal
            # we cannot hook is not a reason to refuse to record.
            pass

    return _run_until_stopped(args, osc, stop_event)


def _run_until_stopped(args, osc, stop_event: threading.Event) -> int:
    """Keep trying to hold a session with the band until asked to stop.

    Previously this process ran exactly once -- scan, connect, stream, exit --
    and the supervising adapter restarted it on failure. That put the retry at
    the wrong level: every retry threw away the whole SDK and BLE stack, and a
    restart is by definition a kill, which is what left the band held open in
    the first place. The radar plugin, which is the stable one in this project,
    owns its own reconnect loop; this now works the same way.

    First connection and reconnection are therefore the same code path, and
    there is no attempt ceiling: as long as the operator wants the band
    recording, this keeps reaching for it.
    """
    attempts = 0
    max_attempts = max(0, int(getattr(args, "max_session_attempts", 0) or 0))
    while not stop_event.is_set():
        attempts += 1
        try:
            exit_code = _run_session(args, osc, stop_event)
        except SystemExit as stop:  # raised deep inside the BLE preflight
            code = stop.code
            exit_code = code if isinstance(code, int) else (EXIT_OK if code is None else EXIT_STREAM_FAILURE)
        except Exception as error:
            # An unforeseen failure must not end the recording either. It is
            # reported in full -- once -- and then treated like any other bad
            # attempt, because a researcher mid-session is better served by a
            # process that keeps reaching for the band than by a traceback.
            _print_json(
                "SESSION_ERROR",
                {"error_type": type(error).__name__, "error": str(error)},
            )
            _print(f"# Unexpected failure in this attempt: {error}", flush=True)
            exit_code = EXIT_STREAM_FAILURE
        finally:
            # Whatever happened, do not walk into the next attempt still
            # holding the band -- that attempt would be refused by the stack.
            _release_active_sensor()

        if stop_event.is_set() or exit_code == EXIT_OK:
            return EXIT_OK
        if exit_code in FATAL_EXIT_CODES:
            # Bluetooth off, or a missing package: retrying changes nothing,
            # and the host has a plain-language message for each of these.
            return exit_code
        if max_attempts and attempts >= max_attempts:
            return exit_code

        _print_json(
            "WAITING",
            {
                "reason_exit_code": exit_code,
                "attempt": attempts,
                "retry_in_seconds": RETRY_DELAY_SECONDS,
                "next_retry_at": _wall_clock_in(RETRY_DELAY_SECONDS),
            },
        )
        _print(f"# Retrying in {RETRY_DELAY_SECONDS:.0f} s ...", flush=True)
        stop_event.wait(RETRY_DELAY_SECONDS)
    return EXIT_OK


def _run_session(args, osc, stop_event: threading.Event) -> int:
    """One attempt: scan, connect, stream, disconnect. Returns an exit code."""
    epoch_anchor, monotonic_anchor = time.time(), time.monotonic()
    def source_now():
        return epoch_anchor + time.monotonic() - monotonic_anchor
    _print_json("SCANNING", {"scan_seconds": args.scan_seconds})
    _print_json("CLOCK", {"epoch_anchor": epoch_anchor, "monotonic_anchor": monotonic_anchor,
                          "timestamp_source": "host_callback_reconstructed"})
    # --- Scan / select device ---
    scanner = Scanner(_brainbit_sensor_families(SensorFamily))

    def _on_sensors(_, sensors):
        # The callback hands over *its* batch, whose numbering restarts each
        # time. Announcing that raw position was the reason a clicked band and
        # the connected band could differ; the shared accumulator gives every
        # band one index that stays valid for the rest of the scan.
        for index, info in _remember_scan_candidates(sensors):
            _print_json("SCAN", _sensor_info_payload(info, index))
    scanner.sensorsChanged = _on_sensors

    _print(f"# Scanning for up to {args.scan_seconds} s ...", flush=True)
    _reset_scan_candidates()
    sensors = _scan_for_target(scanner, args, stop_event)
    if stop_event.is_set():
        return EXIT_OK
    if not sensors:
        message = "No compatible BrainBit-family sensor found."
        _print_json("NO_DEVICE_FOUND", {"message": message, "scan_seconds": int(args.scan_seconds)})
        _print(f"# {message} Exiting.", flush=True)
        return EXIT_NO_DEVICE_FOUND
    sel_idx, info, selection_source = _select_sensor_info(sensors, args)
    if selection_source == "selection_required":
        _print_json("SELECTION_REQUIRED", {"candidate_count": len(sensors)})
        # The parent handles selection by restarting with a stable identity.
        # No active sensor is held while the operator chooses.
        stop_event.wait()
        return EXIT_OK
    if info is None or sel_idx is None:
        target = {
            "serial_number": args.serial_number,
            "device_address": args.device_address,
            "device_name": args.device_name,
            "device_index": args.device_index,
        }
        message = f"Configured BrainBit target not found: {selection_source}"
        _print_json(
            "DEVICE_TARGET_MISSING",
            {"message": message, "target": target, "fallback": None},
        )
        _print(f"# {message}. Refusing to substitute a different headset.", flush=True)
        return EXIT_DEVICE_TARGET_MISSING
    selected_payload = _sensor_info_payload(info, sel_idx)
    selected_payload["selection_source"] = selection_source
    _print_json("DEVICE_SELECTED", selected_payload)
    _print(f"# Connecting to device index {sel_idx} ({selection_source}) ...", flush=True)
    _print_json("CONNECTING", {"index": sel_idx, "selection_source": selection_source})
    # NeuroSDK Scanner.create_sensor() creates and connects the sensor. Calling
    # connect() a second time is an API error on some SDK/device combinations.
    #
    # Guarded because a BLE connect fails for ordinary, transient reasons (the
    # band is still held by a previous session, the adapter is busy, the device
    # stopped advertising between the scan and now). Unguarded, any of those
    # became a bare traceback and exit code 1, which the host could only report
    # as the generic "stopped unexpectedly" -- so an operator was told the
    # process crashed when in truth one connection attempt did not take.
    try:
        sensor = scanner.create_sensor(info)
    except Exception as error:
        _print_json(
            "CONNECT_FAILED",
            {
                "error_type": type(error).__name__,
                "error": str(error),
                "index": sel_idx,
                "selection_source": selection_source,
            },
        )
        _print(f"# Could not connect to the selected band: {error}", flush=True)
        return EXIT_CONNECT_FAILED
    _remember_active_sensor(sensor)
    _print_json("CONNECTED", selected_payload)
    # Pin all retries in this process to the successfully connected device.
    if selected_payload.get("serial"):
        args.serial_number = selected_payload["serial"]
    elif selected_payload.get("address"):
        args.device_address = selected_payload["address"]
    _status("Sensor created and connected successfully.")

    _print_sensor_summary(sensor)
    _debug("Sensor support: features=", _safe(sensor, "features"), "commands=", _safe(sensor, "commands"))

    family_name = _enum_name(_safe(sensor, "sens_family"))

    # Configure BrainBit2-family amplifiers before streaming EEG. Configuration
    # failures are fatal because continuing would create a connected-but-empty
    # process that looks healthy to the host.
    try:
        if family_name in {"LEBrainBit2", "LEBrainBitPro", "LEBrainBitFlex"}:
            amp_param = sensor.amplifier_param
            ch_count = sensor.channels_count
            amp_param.ChGain = [SensorGain.Gain6 for _ in range(ch_count)]
            amp_param.ChSignalMode = [BrainBit2ChannelMode.ChModeNormal for _ in range(ch_count)]
            amp_param.ChResistUse = [True for _ in range(ch_count)]
            amp_param.Current = GenCurrent.GenCurr6nA
            sensor.amplifier_param = amp_param
            _print_json("AMP_CONFIG", {"status": "configured", "channels": int(ch_count)})
    except Exception as exc:
        _print_json("CONFIG_ERROR", {"phase": "amplifier", "error": str(exc)})
        try:
            sensor.disconnect()
        except Exception:
            pass
        return EXIT_STREAM_FAILURE

    supported_channels: list[Any] = []
    channel_index_map: dict[str, int] = {}
    try:
        if family_name in {"LEBrainBit2", "LEBrainBitPro", "LEBrainBitFlex"}:
            supported_channels = list(sensor.supported_channels)
            if not supported_channels:
                raise ValueError("BrainBit2-family sensor reported no supported channels")
            channel_index_map = _supported_channel_index_map(supported_channels)
    except Exception as exc:
        _print_json("CONFIG_ERROR", {"phase": "supported_channels", "error": str(exc)})
        try:
            sensor.disconnect()
        except Exception:
            pass
        return EXIT_STREAM_FAILURE
    if not channel_index_map:
        channel_index_map = {channel: index for index, channel in enumerate(EEG_CHANNELS)}

    # --- Sampling & scaling ---
    fs_hz = _get_sampling_rate_hz(sensor, default_hz=250)
    scale_name = args.eeg_scale
    _scale = {"V": 1.0, "mV": 1e3, "uV": 1e6}[scale_name]
    prec = max(0, int(args.eeg_precision))
    raw_channel_labels = _ordered_channel_labels(channel_index_map)
    derived_enabled = all(channel in channel_index_map for channel in EEG_CHANNELS)
    missing_derived_channels = [channel for channel in EEG_CHANNELS if channel not in channel_index_map]
    if supported_channels:
        channel_rows = [
            {
                "label": _channel_label(channel),
                "index": int(_safe(channel, "Num")),
                "id": _enum_name(_safe(channel, "Id")),
                "type": _enum_name(_safe(channel, "ChType")),
            }
            for channel in supported_channels
        ]
    else:
        channel_rows = [
            {"label": channel, "index": index, "id": channel, "type": "classic_field"}
            for index, channel in enumerate(EEG_CHANNELS)
        ]
    _print_json(
        "CHANNEL_MAP",
        {
            "channels": sorted(channel_rows, key=lambda row: row["index"]),
            "raw_channels": raw_channel_labels,
            "raw_channel_count": len(raw_channel_labels),
            "fs_hz": fs_hz,
            "derived_rate_hz": int(args.process_win_freq),
            "units": scale_name,
            "derived_required_channels": list(EEG_CHANNELS),
            "derived_enabled": derived_enabled,
            "missing_derived_channels": missing_derived_channels,
        },
    )

    # Optional analytics never owns the lifetime of raw EEG acquisition.
    def create_math():
        mls_params = dict(sampling_rate=int(fs_hz), process_win_freq=int(args.process_win_freq),
                          n_first_sec_skipped=int(args.skip_first_sec), fft_window=int(args.fft_window_samples),
                          bipolar_mode=True, channels_number=4, channel_for_analysis=0)
        ads_params = dict(art_bord=110, allowed_percent_artpoints=70, raw_betap_limit=800_000,
                          global_artwin_sec=4, num_wins_for_quality_avg=125, hamming_win_spectrum=True,
                          hanning_win_spectrum=False, total_pow_border=400_000_000, spect_art_by_totalp=True)
        mss_params = dict(n_sec_for_instant_estimation=4, n_sec_for_averaging=2)
        instance = emotional_math.EmotionalMath(lib_settings.MathLibSetting(**mls_params),
                    lib_settings.ArtifactDetectSetting(**ads_params), lib_settings.MentalAndSpectralSetting(**mss_params))
        _configure_emotional_math(instance, calibration_sec=args.calibration_sec,
                                  skip_windows=args.nwins_skip_after_artifact)
        versions = {}
        for package in ("pyneurosdk2", "pyem-st-artifacts"):
            try:
                versions[package] = version(package)
            except PackageNotFoundError:
                versions[package] = "unknown"
        _print_json("EMO_INIT", {"versions": versions, "math_settings": mls_params,
                    "artifact_settings": ads_params, "mental_settings": mss_params,
                    "derivations": ["T3-O1", "T4-O2"], "input_units": "V",
                    "calibration_sec": args.calibration_sec,
                    "skip_windows_after_artifact": args.nwins_skip_after_artifact, "squared_spectrum": True})
        return instance

    math_lib = None
    if derived_enabled:
        try:
            math_lib = create_math()
        except Exception as error:
            _print_json("EMO_INIT_FAIL", {"error": str(error), "raw_stream_continues": True})
    else:
        _print_json("DERIVED_DISABLED", {"reason": "missing_required_channels",
                    "missing_channels": missing_derived_channels, "raw_stream_continues": True})

    # ---------- smoothing for console/OSC EEG ----------
    detrenders = {channel: DCDetrender(alpha=args.detrend_alpha) for channel in raw_channel_labels}
    notchers: Dict[str, Optional[NotchIIR]] = {ch: None for ch in detrenders}
    if args.mains_hz in (50, 60):
        for ch in notchers: notchers[ch] = NotchIIR(fs_hz, args.mains_hz, Q=30.0)

    # ---------- state & helpers ----------
    calib_started = False
    calib_finished = False
    calib_start_time = 0.0
    last_prog_time = 0.0
    last_prog_value = 0.0
    last_reported_progress: float | None = None
    art_on, art_start = False, 0.0
    last_artifact_state: tuple[int, int] | None = None

    q_smooth: Dict[str, float] = {}

    # --------- emit helpers ---------
    def _emit_spectral(specs: List[Any], timestamps: List[float]):
        rows: list[dict[str, float]] = []
        for sp, ts in zip(specs, timestamps, strict=True):
            delta = _percent_to_ratio(_result_value(sp, "delta", "Delta"))
            theta = _percent_to_ratio(_result_value(sp, "theta", "Theta"))
            alpha = _percent_to_ratio(_result_value(sp, "alpha", "Alpha"))
            beta = _percent_to_ratio(_result_value(sp, "beta", "Beta"))
            gamma = _percent_to_ratio(_result_value(sp, "gamma", "Gamma"))
            if None in (delta, theta, alpha, beta, gamma): continue
            rows.append(
                {
                    "ts": float(ts),
                    "delta": round(delta, 6),
                    "theta": round(theta, 6),
                    "alpha": round(alpha, 6),
                    "beta": round(beta, 6),
                    "gamma": round(gamma, 6),
                }
            )
        if not rows:
            return
        fields = ("delta", "theta", "alpha", "beta", "gamma")
        _print_json(
            "BANDS_BATCH",
            {
                "ts": rows[0]["ts"],
                "end_ts": rows[-1]["ts"],
                "sample_count": len(rows),
                "channels": list(fields),
                "samples": [[row[field] for field in fields] for row in rows],
                "timestamps": [row["ts"] for row in rows],
                "validity": "uncertain" if last_artifact_state != (0, 0) or not calib_finished or len(rows) > 1 else "valid",
                "validity_scope": "conservative_output_batch",
            },
        )
        latest = rows[-1]
        for name, field in (("Delta", "delta"), ("Theta", "theta"), ("Alpha", "alpha"), ("Beta", "beta"), ("Gamma", "gamma")):
            _send_num(osc, "BANDS", name, latest[field])
            _send_root(osc, name, latest[field])

    def _emit_mind(minds: List[Any], timestamps: List[float]):
        rows: list[dict[str, float]] = []
        for md, ts in zip(minds, timestamps, strict=True):
            inst_att = _percent_to_ratio(_result_value(md, "inst_attention", "Inst_Attention"))
            inst_rel = _percent_to_ratio(_result_value(md, "inst_relaxation", "Inst_Relaxation"))
            rel_att = _percent_to_ratio(_result_value(md, "rel_attention", "Rel_Attention"))
            rel_rel = _percent_to_ratio(_result_value(md, "rel_relaxation", "Rel_Relaxation"))
            if None in (inst_att, inst_rel, rel_att, rel_rel): continue
            rows.append(
                {
                    "ts": float(ts),
                    "Inst_Attention": round(inst_att, 6),
                    "Inst_Relaxation": round(inst_rel, 6),
                    "Rel_Attention": round(rel_att, 6),
                    "Rel_Relaxation": round(rel_rel, 6),
                }
            )
        if not rows:
            return
        fields = ("Inst_Attention", "Inst_Relaxation", "Rel_Attention", "Rel_Relaxation")
        _print_json(
            "MENTAL_BATCH",
            {
                "ts": rows[0]["ts"],
                "end_ts": rows[-1]["ts"],
                "sample_count": len(rows),
                "channels": list(fields),
                "samples": [[row[field] for field in fields] for row in rows],
                "timestamps": [row["ts"] for row in rows],
                "validity": "uncertain" if last_artifact_state != (0, 0) or not calib_finished or len(rows) > 1 else "valid",
                "validity_scope": "conservative_output_batch",
            },
        )
        latest = rows[-1]
        for name in fields:
            _send_num(osc, "MENTAL", name, latest[name])
            _send_root(osc, name, latest[name])

    # ---------- callbacks ----------
    callback_failure: dict[str, str] = {}
    callback_failure_lock = threading.Lock()
    callback_failed = threading.Event()
    disconnected = threading.Event()
    timestamp_estimator = SourceTimestampEstimator(fs_hz)
    spectral_timestamp_estimator = SourceTimestampEstimator(args.process_win_freq)
    mental_timestamp_estimator = SourceTimestampEstimator(args.process_win_freq)
    eeg_buffer_lock = threading.Lock()
    pending_eeg: list[dict[str, Any]] = []
    last_eeg_emit_at = 0.0
    eeg_queue_overflow_dropped_total = 0
    eeg_stream_started_at_monotonic: float | None = None
    eeg_total_samples_emitted = 0
    calib_stalled = False

    def _emit_eeg_batch(*, force: bool = False, now: float | None = None) -> None:
        nonlocal last_eeg_emit_at
        nonlocal eeg_stream_started_at_monotonic, eeg_total_samples_emitted
        current = float(now if now is not None else time.monotonic())
        with eeg_buffer_lock:
            if not pending_eeg:
                return
            if not force and last_eeg_emit_at and current - last_eeg_emit_at < EEG_STDOUT_INTERVAL_SECONDS:
                return
            rows = list(pending_eeg)
            pending_eeg.clear()
            last_eeg_emit_at = current

        # Measured throughput, not the nominal 250Hz: a QC field an operator
        # can see in the live stream itself, using the same method
        # (samples / elapsed wall time) as the separate diagnose_backends.py
        # A/B tool's effective_wall_rate_hz.
        if eeg_stream_started_at_monotonic is None:
            eeg_stream_started_at_monotonic = current
        eeg_total_samples_emitted += len(rows)
        measured_hz = _measured_hz(eeg_total_samples_emitted, current - eeg_stream_started_at_monotonic)

        _print_json(
            "EEG_BATCH",
            {
                "ts": rows[0]["timestamp"],
                "received_epoch": rows[-1]["received_epoch"],
                "received_monotonic": rows[-1]["received_monotonic"],
                "end_ts": rows[-1]["timestamp"],
                "sample_interval_sec": timestamp_estimator.sample_interval,
                "sample_count": len(rows),
                "channels": list(raw_channel_labels),
                "samples": [row["sample"] for row in rows],
                "timestamps": [row["timestamp"] for row in rows],
                "packs": [row["pack"] for row in rows],
                "markers": [row["marker"] for row in rows],
                "packet_gap_frames": sum(int(row.get("packet_gap_before") or 0) for row in rows),
                "packet_gap_frames_total": timestamp_estimator.packet_gap_frames_total,
                "packet_counter_reset_total": timestamp_estimator.packet_counter_reset_total,
                "packet_counter_events": [
                    {
                        "sample_index": index,
                        **row["packet_counter_event"],
                    }
                    for index, row in enumerate(rows)
                    if row.get("packet_counter_event")
                ],
                "packet_shapes": sorted({row["packet_shape"] for row in rows}),
                "units": scale_name,
                "source_units": "V",
                "processing": "unit_scale_only",
                "timestamp_source": "host_callback_reconstructed",
                "preview": rows[-1]["preview"],
                "measured_hz": round(measured_hz, 2) if measured_hz is not None else None,
                "queue_overflow_dropped_total": eeg_queue_overflow_dropped_total,
            },
        )

    def _record_callback_failure(name: str, error: Exception) -> None:
        with callback_failure_lock:
            if callback_failure:
                return
            callback_failure.update(
                {
                    "phase": name,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
        try:
            _emit_eeg_batch(force=True)
        except Exception:
            pass
        _print_json("CALLBACK_ERROR", dict(callback_failure))
        callback_failed.set()
        stop_event.set()

    def _guard_callback(name: str, callback):
        def guarded(*callback_args):
            try:
                callback(*callback_args)
            except Exception as error:
                _record_callback_failure(name, error)
        return guarded

    def on_state(s, state):
        if any(value in _enum_name(state).lower() for value in ("disconnect", "outofrange")):
            disconnected.set()
            _print_json("DISCONNECTED", {"state": _enum_name(state)})
        _print_json("STATE", {"state": _enum_name(state)})
        _status("Sensor state:", _enum_name(state))

    def on_battery(s, pct):
        _print_json("BATTERY", {"percent": _json_number(pct)})
        _send_num(osc, "BATTERY", "percent", pct)
        _pretty_line("BATTERY", f"Battery level is {pct}%")

    def on_resist(s, data):
        ts = source_now()
        for pkt in _iter(data):
            decoded, packet_shape = _decode_packet_channels(
                pkt,
                channel_index_map,
                require_finite=False,
            )
            row = {
                "ts": ts,
                "pack": _json_number(_safe(pkt, "PackNum")),
                **{channel: _json_number(decoded[channel]) for channel in raw_channel_labels},
                "units": "Ohm",
                "packet_shape": packet_shape,
            }
            referents = _safe(pkt, "Referents")
            if referents is not None:
                row["referents_ohm"] = [_json_number(value) for value in list(referents)]
            row["open_channels"] = [channel for channel in raw_channel_labels if row[channel] is None]
            _print_json("RESIST", row)
            _status("Resist packet", _format_values(**{key: row[key] for key in ("pack", *raw_channel_labels)}))
            for channel in raw_channel_labels:
                _send_num(osc, "RESIST", channel, row[channel])
                quality = _resistance_to_quality(row[channel])
                previous = q_smooth.get(channel, quality)
                smoothed = (0.2 * quality) + (0.8 * previous)
                q_smooth[channel] = smoothed
                _send_num(osc, "QUALITY", channel, smoothed)
            quality_row = {
                **{channel: round(q_smooth.get(channel, 0.0), 3) for channel in raw_channel_labels},
                "units": "ratio",
                "resistance_upper_ohm": RESISTANCE_UPPER_OHM,
                "quality_model": "linear_diagnostic_only",
            }
            _print_json("QUALITY", quality_row)
            _pretty_line("QUALITY", _format_values(**quality_row))

    def on_signal(s, data):
        nonlocal calib_started, calib_finished, calib_start_time
        nonlocal last_prog_time, last_prog_value, art_on, art_start, calib_stalled
        nonlocal last_reported_progress, last_artifact_state
        nonlocal eeg_queue_overflow_dropped_total, math_lib
        now = source_now()
        monotonic_now = time.monotonic()
        decoded_packets: list[tuple[Any, dict[str, float], str]] = []
        decode_errors: list[str] = []
        for pkt in _iter(data):
            try:
                values, packet_shape = _decode_packet_channels(pkt, channel_index_map)
            except ValueError as error:
                decode_errors.append(str(error))
                continue
            decoded_packets.append((pkt, values, packet_shape))

        if not decoded_packets:
            detail = decode_errors[0] if decode_errors else "callback contained no packets"
            raise ValueError(f"No valid EEG frames were decoded: {detail}")
        sample_timestamps, packet_timing = timestamp_estimator.for_packets(
            [_safe(packet, "PackNum") for packet, _, _ in decoded_packets],
            now,
        )
        packet_events = [
            event
            for event in packet_timing
            if event.get("counter_event") in {"gap", "reset", "duplicate", "unknown"}
        ]
        packet_gap_frames = sum(int(event.get("gap_before") or 0) for event in packet_timing)
        if decode_errors or packet_events:
            warning: dict[str, Any] = {
                "phase": "signal_integrity",
                "discarded_frames": len(decode_errors),
                "packet_gap_frames": packet_gap_frames,
                "packet_gap_frames_total": timestamp_estimator.packet_gap_frames_total,
                "packet_counter_reset_total": timestamp_estimator.packet_counter_reset_total,
                "packet_counter_events": packet_events[:16],
            }
            if decode_errors:
                warning["decode_error"] = decode_errors[0]
            _print_json("DATA_WARNING", warning)

        raw_channels = []
        output_rows: list[dict[str, Any]] = []
        for timestamp, timing, (pkt, values, packet_shape) in zip(
            sample_timestamps,
            packet_timing,
            decoded_packets,
            strict=True,
        ):
            raw_scaled = {channel: values[channel] * _scale for channel in raw_channel_labels}
            preview = {}
            for channel, raw_value in raw_scaled.items():
                display_value = detrenders[channel].step(raw_value)
                if notchers[channel]:
                    display_value = notchers[channel].step(display_value)
                preview[channel] = round(display_value, prec)
                _send_num(osc, "EEG", channel, preview[channel])
            output_rows.append(
                {
                    "timestamp": timestamp,
                    "received_epoch": time.time(),
                    "received_monotonic": monotonic_now,
                    "pack": _json_number(_safe(pkt, "PackNum")),
                    "marker": _json_number(_safe(pkt, "Marker")),
                    "packet_gap_before": int(timing.get("gap_before") or 0),
                    "packet_counter_event": (
                        {key: value for key, value in timing.items() if key != "gap_before"}
                        if timing.get("counter_event")
                        else None
                    ),
                    "sample": [raw_scaled[channel] for channel in raw_channel_labels],
                    "preview": preview,
                    "packet_shape": packet_shape,
                }
            )

        with eeg_buffer_lock:
            pending_eeg.extend(output_rows)
            # The consumer (stdout reader on the host side) has stalled long
            # enough that the throttled flush below could not keep up.
            overflow_dropped = _apply_eeg_queue_cap(pending_eeg)
            eeg_queue_overflow_dropped_total += overflow_dropped
        if overflow_dropped:
            _print_json(
                "DATA_WARNING",
                {
                    "phase": "eeg_queue_overflow",
                    "dropped_samples": overflow_dropped,
                    "dropped_samples_total": eeg_queue_overflow_dropped_total,
                },
            )
        _emit_eeg_batch(now=monotonic_now)
        _debug("Signal callback received", len(output_rows), "valid raw frames")

        if math_lib is None:
            return
        if decode_errors or packet_events or overflow_dropped:
            # Do not join an FFT/calibration window across missing or ambiguous samples.
            calib_started = calib_finished = calib_stalled = False
            last_reported_progress = last_artifact_state = None
            spectral_timestamp_estimator.last_timestamp = None
            mental_timestamp_estimator.last_timestamp = None
            _print_json("CALIB", {"event": "RESET", "reason": "signal_discontinuity", "ts": now})
            try:
                math_lib = create_math()
            except Exception as error:
                math_lib = None
                _print_json("EMO_INIT_FAIL", {"error": str(error), "raw_stream_continues": True})
            return
        try:
            raw_channels = [support_classes.RawChannels(values["T3"] - values["O1"],
                            values["T4"] - values["O2"]) for _, values, _ in decoded_packets]
            # Drain each produced window before processing the next input frame.
            # Otherwise a callback backlog's final artifact flag could label
            # earlier, held SDK outputs as clean.
            for bipolar, timestamp in zip(raw_channels, sample_timestamps, strict=True):
                process_derived([bipolar], [timestamp], monotonic_now)
        except Exception as error:
            math_lib = None
            _print_json("EMO_INIT_FAIL", {"phase": "processing", "error": str(error), "raw_stream_continues": True})

    def process_derived(raw_channels, sample_timestamps, monotonic_now):
        nonlocal calib_started, calib_finished, calib_start_time, calib_stalled
        nonlocal last_prog_time, last_prog_value, last_reported_progress, last_artifact_state, art_on, art_start
        # Start calibration once (non-blocking)
        if not calib_started:
            _status("Starting emotion calibration.")
            math_lib.start_calibration()
            calib_started = True
            calib_start_time = monotonic_now
            last_prog_time = monotonic_now
            last_prog_value = 0.0
            _print_json("CALIB", {"event": "START", "target_sec": int(args.calibration_sec)})
            _send_num(osc, "CALIB", "Started", 1.0)

        _debug("Pushing", len(raw_channels), "raw bipolar frames into EmotionalMath")
        _push_bipolar_samples(math_lib, raw_channels)
        math_lib.process_data_arr()

        # Artifacts flags (always)
        both_art = 1.0 if math_lib.is_both_sides_artifacted() else 0.0
        seq_art  = 1.0 if math_lib.is_artifacted_sequence() else 0.0
        _send_num(osc, "ARTIFACT", "Both", both_art)
        _send_num(osc, "ARTIFACT", "Seq",  seq_art)
        artifact_state = (int(both_art), int(seq_art))
        if artifact_state != last_artifact_state:
            _print_json("ARTIFACT", {"both_now": artifact_state[0], "sequence": artifact_state[1]})
            last_artifact_state = artifact_state
        if both_art or seq_art:
            if not art_on:
                art_on = True; art_start = monotonic_now
        else:
            art_on = False

        if not calib_finished:
            progress = math_lib.get_calibration_percents()
            if progress is not None:
                progress_value = float(progress)
                if progress_value >= last_prog_value + 0.25:
                    last_prog_value = progress_value
                    last_prog_time = monotonic_now
                if last_reported_progress is None or abs(progress_value - last_reported_progress) >= 0.25:
                    last_reported_progress = progress_value
                    _print_json("CALIB", {"progress_percent": progress_value})
                    _send_num(osc, "CALIB", "Progress", max(0.0, min(1.0, progress_value / 100.0)))
            if math_lib.calibration_finished():
                calib_finished = True
                _print_json("CALIB", {"event": "FINISHED"})
                _send_num(osc, "CALIB", "Finished", 1.0)

        if calib_started and not calib_finished and not calib_stalled:
            reason = None
            if (monotonic_now - calib_start_time) >= float(args.calib_max_sec):
                reason = "timeout"
            elif (monotonic_now - last_prog_time) >= float(args.calib_stall_sec):
                reason = "stall"
            elif args.force_on_artifacts and art_on and ((monotonic_now - art_start) >= float(args.art_streak_sec)):
                reason = "artifact_streak"
            if reason:
                # EmotionalMath has no force-finish API. Keep raw acquisition
                # running, but do not label uncalibrated metrics as valid.
                calib_stalled = True
                _print_json(
                    "CALIB",
                    {
                        "event": "STALLED",
                        "reason": reason,
                        "last_progress_percent": round(last_prog_value, 2),
                    },
                )

        if not calib_finished:
            return

        source_timestamp = sample_timestamps[-1]
        specs = list(math_lib.read_spectral_data_percents_arr() or [])
        if specs:
            _emit_spectral(
                specs,
                spectral_timestamp_estimator.for_batch(len(specs), source_timestamp),
            )

        minds = list(math_lib.read_mental_data_arr() or [])
        if minds:
            _emit_mind(
                minds,
                mental_timestamp_estimator.for_batch(len(minds), source_timestamp),
            )

    def on_fpg(s, data):
        ts = time.time()
        for pkt in _iter(data):
            row = {"ts": round(ts, 3), "pack": _safe(pkt, "PackNum"),
                   "IrAmplitude": _safe(pkt, "IrAmplitude"), "RedAmplitude": _safe(pkt, "RedAmplitude")}
            _print_json("FPG", row)
            _send_num(osc, "FPG", "IrAmplitude", row["IrAmplitude"])
            _send_num(osc, "FPG", "RedAmplitude", row["RedAmplitude"])

    def on_mems(s, data):
        ts = time.time()
        for pkt in _iter(data):
            acc = _safe(pkt, "Accelerometer"); gyr = _safe(pkt, "Gyroscope")
            row = {"ts": round(ts, 3), "pack": _safe(pkt, "PackNum"),
                   "accel": {"x": _safe(acc, "X", _safe(acc, "x")), "y": _safe(acc, "Y", _safe(acc, "y")), "z": _safe(acc, "Z", _safe(acc, "z"))} if acc is not None else None,
                   "gyro":  {"x": _safe(gyr, "X", _safe(gyr, "x")), "y": _safe(gyr, "Y", _safe(gyr, "y")), "z": _safe(gyr, "Z", _safe(gyr, "z"))} if gyr is not None else None}
            _print_json("MEMS", row)
            if row["accel"]:
                _send_num(osc, "MEMS", "AccelX", row["accel"]["x"]); _send_num(osc, "MEMS", "AccelY", row["accel"]["y"]); _send_num(osc, "MEMS", "AccelZ", row["accel"]["z"])
            if row["gyro"]:
                _send_num(osc, "MEMS", "GyroX", row["gyro"]["x"]); _send_num(osc, "MEMS", "GyroY", row["gyro"]["y"]); _send_num(osc, "MEMS", "GyroZ", row["gyro"]["z"])

    # Native ctypes callbacks do not propagate Python exceptions to the main
    # thread. Guard every callback so failures become structured and terminate
    # the acquisition loop with a non-zero exit code.
    sensor.sensorStateChanged = _guard_callback("state", on_state)
    sensor.batteryChanged = _guard_callback("battery", on_battery)
    if sensor.is_supported_feature(SensorFeature.Resist):
        sensor.resistDataReceived = _guard_callback("resistance", on_resist)
    if sensor.is_supported_feature(SensorFeature.FPG):
        sensor.fpgDataReceived = _guard_callback("fpg", on_fpg)
    if sensor.is_supported_feature(SensorFeature.MEMS):
        sensor.memsDataReceived = _guard_callback("mems", on_mems)
    if sensor.is_supported_feature(SensorFeature.Signal):
        sensor.signalDataReceived = _guard_callback("signal", on_signal)

    # device info (immediate)
    _print_json("DEVICE", {
        "family": _enum_name(_safe(sensor, "sens_family")),
        "name": _safe(sensor, "name"),
        "address": _safe(sensor, "address"),
        "serial_number": _safe(sensor, "serial_number"),
        "fs_hz": fs_hz, "process_win_freq_hz": int(args.process_win_freq), "fft_window_samples": int(args.fft_window_samples),
        "scale": scale_name,
        "raw_processing": "unit_scale_only",
        "raw_channels": list(raw_channel_labels),
        "derived_enabled": derived_enabled,
        "missing_derived_channels": missing_derived_channels,
        "supported_channels": [
            {"label": label, "index": index}
            for label, index in sorted(channel_index_map.items(), key=lambda item: item[1])
        ],
    })

    # stage runner
    def _raise_callback_failure() -> None:
        if callback_failed.is_set():
            phase = callback_failure.get("phase", "unknown")
            message = callback_failure.get("error", "unknown callback error")
            raise RuntimeError(f"{phase} callback failed: {message}")

    def _run_stage(cmd_start: SensorCommand, cmd_stop: SensorCommand, seconds: int, label: str):
        if seconds <= 0: return
        if not sensor.is_supported_command(cmd_start):
            _print(f"# {label}: command not supported, skipping.", flush=True); return
        _print(f"# {label}: START ({seconds}s)", flush=True)
        _print_json("STREAM", {"stream": label.lower(), "event": "START"})
        _debug(f"Executing command {cmd_start} for {label}")
        sensor.exec_command(cmd_start)
        try:
            t_end = time.monotonic() + seconds
            while time.monotonic() < t_end and not stop_event.is_set() and not callback_failed.is_set():
                callback_failed.wait(0.05)
        finally:
            sensor.exec_command(cmd_stop)
        _debug(f"Stopping command {cmd_stop} for {label}")
        _print_json("STREAM", {"stream": label.lower(), "event": "STOP"})
        _print(f"# {label}: STOP", flush=True)
        _raise_callback_failure()

    failure_exit_code: int | None = None
    try:
        if not args.no_resist and sensor.is_supported_feature(SensorFeature.Resist):
            _run_stage(SensorCommand.StartResist, SensorCommand.StopResist, args.resist_seconds, "RESIST")
        if not args.no_fpg and sensor.is_supported_feature(SensorFeature.FPG):
            _run_stage(SensorCommand.StartFPG, SensorCommand.StopFPG, args.fpg_seconds, "FPG")
        if not args.no_mems and sensor.is_supported_feature(SensorFeature.MEMS):
            _run_stage(SensorCommand.StartMEMS, SensorCommand.StopMEMS, args.mems_seconds, "MEMS")
        if sensor.is_supported_command(SensorCommand.StartSignal):
            dur = args.signal_seconds
            _print("# EEG: START (Ctrl+C to stop)" if dur == 0 else f"# EEG: START ({dur}s)", flush=True)
            _print_json("STREAM", {"stream": "eeg", "event": "START", "fs_hz": fs_hz})
            sensor.exec_command(SensorCommand.StartSignal)
            t_end = (time.monotonic() + dur) if dur > 0 else None
            while (
                not stop_event.is_set()
                and not callback_failed.is_set()
                and not disconnected.is_set()
                and (t_end is None or time.monotonic() < t_end)
            ):
                callback_failed.wait(0.05)
            sensor.exec_command(SensorCommand.StopSignal)
            _emit_eeg_batch(force=True)
            _print_json("STREAM", {"stream": "eeg", "event": "STOP"})
            _print("# EEG: STOP", flush=True)
            _raise_callback_failure()
            if disconnected.is_set() and not stop_event.is_set():
                return EXIT_CONNECT_FAILED
        else:
            raise RuntimeError("EEG StartSignal command is not supported by the selected sensor")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        failure_exit_code = EXIT_CALLBACK_FAILURE if callback_failed.is_set() else EXIT_STREAM_FAILURE
        _print_json(
            "STREAM_ERROR",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "callback_failure": bool(callback_failed.is_set()),
            },
        )
        _print(f"# ERROR during streaming: {e}", flush=True)
    finally:
        try:
            sensor.signalDataReceived = None
            sensor.resistDataReceived = None
            sensor.fpgDataReceived = None
            sensor.memsDataReceived = None
        except Exception:
            pass
        try:
            sensor.disconnect()
        except Exception:
            pass
        _print("# Disconnected.", flush=True)

    return failure_exit_code if failure_exit_code is not None else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
