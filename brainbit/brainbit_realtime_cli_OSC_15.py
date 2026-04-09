# WORKING!
# based on: https://chatgpt.com/g/g-p-68a4726e0b8c8191920fd5f3a430885d-kristian/c/68d4004b-4b68-8324-97ae-79a382299d7e?model=gpt-5-thinking

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
brainbit_realtime_cli_OSC_15.py

Fixes missing MENTAL/BANDS emission by mirroring BrainBit's
`sample_brainbit_emotions.py` settings & flow:

- Proper EmotionalMath params (Hamming on, Hanning off, total_pow_border high,
  spect_art_by_totalp True, squared_spectrum True, channels_number=4,
  bipolar_mode True).
- Start calibration once; after it finishes (or is force-finished),
  emit SpectralDataPercents and MindData on each analysis tick.
- Send both namespaced and root-level OSC + print JSON lines.

Requires:
  pip install pyneurosdk2 python-osc pyem-st-artifacts
"""

import argparse, json, math, platform, re, signal as os_signal, threading, time, subprocess, sys
import importlib.util
from typing import Any, Iterable, List, Optional, Tuple, Dict

REQUIRED_MODULES = [
    ("neurosdk", "pyneurosdk2"),
    ("pythonosc", "python-osc"),
    ("em_st_artifacts", "pyem-st-artifacts"),
]

def _is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _install_package(package_name: str) -> None:
    print(f"[SETUP] Installing {package_name} ...", flush=True)
    subprocess.check_call([
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        package_name,
    ])


def _ensure_requirements() -> None:
    missing = []
    for module_name, package_name in REQUIRED_MODULES:
        if not _is_module_available(module_name):
            missing.append((module_name, package_name))
    if missing:
        print(f"[SETUP] Missing required libraries: {', '.join(m for m,_ in missing)}", flush=True)
        for module_name, package_name in missing:
            try:
                _install_package(package_name)
            except Exception as exc:
                raise SystemExit(f"[ERROR] Could not install {package_name}: {exc}")
    for module_name, _ in REQUIRED_MODULES:
        if not _is_module_available(module_name):
            raise SystemExit(f"[ERROR] Missing required module {module_name} after installation")
    print(f"[SETUP] Required libraries are installed: {', '.join(m for m,_ in REQUIRED_MODULES)}", flush=True)


_ensure_requirements()

# --- NeuroSDK2
from neurosdk.scanner import Scanner
from neurosdk.cmn_types import SensorFamily, SensorFeature, SensorCommand, SensorGain, BrainBit2ChannelMode, GenCurrent

# --- OSC
from pythonosc.udp_client import SimpleUDPClient

# --- Emotions (official)
from em_st_artifacts.utils import lib_settings, support_classes
from em_st_artifacts import emotional_math


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

DEBUG = False
PRETTY = False


def _print_json(tag: str, data: dict):
    print(f"{tag} {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}", flush=True)


def _set_output_mode(debug: bool, pretty: bool) -> None:
    global DEBUG, PRETTY
    DEBUG = debug
    PRETTY = pretty
    _status(f"Pretty output={PRETTY}, Debug mode={DEBUG}")


def _status(*args, **kwargs):
    if PRETTY or DEBUG:
        print("[STATUS]", *args, **kwargs, flush=True)


def _debug(*args, **kwargs):
    if DEBUG:
        print("[DEBUG]", *args, **kwargs, flush=True)


def _warn(*args, **kwargs):
    print("[WARN]", *args, **kwargs, flush=True)


def _pretty_line(label: str, line: str) -> None:
    if PRETTY:
        print(f"[{label}] {line}", flush=True)


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
        "features": [f.name for f in features] if isinstance(features, (list, tuple)) else str(features),
        "commands": [c.name for c in commands] if isinstance(commands, (list, tuple)) else str(commands),
    }
    _status("Connected sensor info:")
    for key, value in sensor_info.items():
        _status(f"  {key}: {value}")


def _format_values(**kwargs) -> str:
    return ", ".join(f"{k}={v}" for k, v in kwargs.items())


def _send_num(osc: SimpleUDPClient, label: str, name: str, val):
    if osc is None or val is None: return
    try:
        v = float(val)
        if math.isnan(v): return
        osc.send_message(f"/BrainBit/{label}/{name}", v)
    except Exception:
        pass

def _send_root(osc: SimpleUDPClient, name: str, val):
    if osc is None or val is None: return
    try:
        v = float(val)
        if math.isnan(v): return
        osc.send_message(f"/BrainBit/{name}", v)
    except Exception:
        pass

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
def _start_scan_or_explain(scanner, seconds: int):
    try:
        scanner.start()
    except Exception as e:
        msg = str(e)
        if "Code 103" in msg or "BLE adapter not found or disabled" in msg:
            print("# FATAL: BLE adapter not found or disabled.", flush=True)
            if platform.system() == "Darwin":
                print("# macOS checklist:\n#  1) Bluetooth ON.\n#  2) Privacy → Bluetooth: allow your terminal.\n#  3) Headband not connected elsewhere.\n#  4) If stuck: sudo killall -9 bluetoothd; toggle BT.\n#  5) which python3", flush=True)
            raise SystemExit(103)
        else:
            raise
    try:
        time.sleep(max(1, seconds))
    finally:
        scanner.stop()


# ----------------- main -----------------
def main():
    ap = argparse.ArgumentParser(description="BrainBit CLI + OSC + Emotions (Bands + Mind) with calibration watchdog")
    ap.add_argument("--scan-seconds", type=int, default=5)
    ap.add_argument("--device-index", type=int, default=0)

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

    args = ap.parse_args()
    _set_output_mode(debug=args.debug, pretty=args.pretty)
    osc = None if args.no_osc else SimpleUDPClient(args.osc_host, int(args.osc_port))

    # --- Scan / select device ---
    scanner = Scanner([SensorFamily.LEBrainBit, SensorFamily.LEBrainBitBlack, SensorFamily.LEBrainBit2])

    def _on_sensors(_, sensors):
        for idx, info in enumerate(sensors):
            _print_json("SCAN", {
                "index": idx,
                "name": _safe(info, "Name"),
                "family": _enum_name(_safe(info, "SensFamily")),
                "address": _safe(info, "Address"),
                "serial": _safe(info, "SerialNumber"),
                "pairing_required": _safe(info, "PairingRequired"),
                "rssi": _safe(info, "RSSI"),
            })
    scanner.sensorsChanged = _on_sensors

    stop_event = threading.Event()
    def _on_sigint(signum, frame):
        print("\n# Ctrl+C — stopping streams ...", flush=True)
        stop_event.set()
    os_signal.signal(os_signal.SIGINT, _on_sigint)

    print(f"# Scanning for {args.scan_seconds} s ...", flush=True)
    _start_scan_or_explain(scanner, args.scan_seconds)
    sensors = scanner.sensors()
    if not sensors:
        print("# No compatible BrainBit-family sensor found. Exiting.", flush=True)
        return
    sel_idx = max(0, min(args.device_index, len(sensors)-1))
    info = sensors[sel_idx]
    print(f"# Connecting to device index {sel_idx} ...", flush=True)
    sensor = scanner.create_sensor(info)

    try:
        if hasattr(sensor, "connect"):
            sensor.connect(); time.sleep(0.5)
            _status("Sensor connected successfully.")
    except Exception as exc:
        _warn("Sensor connect warning:", exc)

    _print_sensor_summary(sensor)
    _debug("Sensor support: features=", _safe(sensor, "features"), "commands=", _safe(sensor, "commands"))

    # Configure BrainBit2 amplifier before streaming EEG
    try:
        if sensor.sens_family in (SensorFamily.LEBrainBit2, SensorFamily.LEBrainBitPro, SensorFamily.LEBrainBitFlex):
            amp_param = sensor.amplifier_param
            ch_count = sensor.channels_count
            amp_param.ChGain = [SensorGain.Gain6 for _ in range(ch_count)]
            amp_param.ChSignalMode = [BrainBit2ChannelMode.ChModeNormal for _ in range(ch_count)]
            amp_param.ChResistUse = [True for _ in range(ch_count)]
            amp_param.Current = GenCurrent.GenCurr6nA
            sensor.amplifier_param = amp_param
    except Exception:
        pass

    # --- Sampling & scaling ---
    fs_hz = _get_sampling_rate_hz(sensor, default_hz=250)
    scale_name = args.eeg_scale
    _scale = {"V": 1.0, "mV": 1e3, "uV": 1e6}[scale_name]
    prec = max(0, int(args.eeg_precision))

    # ---------- Emotions init (EXACT like the official sample) ----------
    mls = lib_settings.MathLibSetting(
        sampling_rate=int(fs_hz),
        process_win_freq=int(args.process_win_freq),
        n_first_sec_skipped=int(args.skip_first_sec),
        fft_window=int(args.fft_window_samples),
        bipolar_mode=True,
        channels_number=4,
        channel_for_analysis=0
    )
    ads = lib_settings.ArtifactDetectSetting(
        art_bord=110,
        allowed_percent_artpoints=70,
        raw_betap_limit=800_000,
        global_artwin_sec=4,
        num_wins_for_quality_avg=125,
        hamming_win_spectrum=True,   # <-- as in sample
        hanning_win_spectrum=False,  # <-- as in sample
        total_pow_border=400_000_000,
        spect_art_by_totalp=True
    )
    mss  = lib_settings.MentalAndSpectralSetting(2, 4)

    try:
        math_lib = emotional_math.EmotionalMath(mls, ads, mss)
    except Exception as e:
        _print_json("EMO_INIT_FAIL", {"error": str(e)})
        raise SystemExit("# FATAL: EmotionalMath init failed")

    math_lib.set_calibration_length(int(args.calibration_sec))
    math_lib.set_mental_estimation_mode(False)  # as sample
    math_lib.set_skip_wins_after_artifact(int(args.nwins_skip_after_artifact))
    # Same pattern as sample: zero some waves OFF (0) — we keep all enabled (0 = NOT zeroed)
    # In their sample they used (True, 0,1,1,1,0) which zeros Theta/Alpha/Beta (set to 1).
    # To avoid accidentally zeroing anything, pass all zeros:
    math_lib.set_zero_spect_waves(True, 0, 0, 0, 0, 0)
    math_lib.set_spect_normalization_by_bands_width(True)

    _print_json("EMO_INIT", {
        "fs_hz": fs_hz,
        "process_win_freq_hz": int(args.process_win_freq),
        "fft_window_samples": int(args.fft_window_samples),
        "bipolar_mode": True,
        "channels_number": 4,
        "eeg_scale": scale_name
    })

    # ---------- smoothing for console/OSC EEG ----------
    detrenders = {ch: DCDetrender(alpha=args.detrend_alpha) for ch in ("O1","O2","T3","T4")}
    notchers: Dict[str, Optional[NotchIIR]] = {ch: None for ch in detrenders}
    if args.mains_hz in (50, 60):
        for ch in notchers: notchers[ch] = NotchIIR(fs_hz, args.mains_hz, Q=30.0)

    # ---------- state & helpers ----------
    calib_started = False
    calib_finished = False
    calib_forced = False
    calib_start_time = 0.0
    last_prog_time = 0.0
    last_prog_value = 0.0
    art_on, art_start = False, 0.0

    last_bands: Optional[Tuple[float,float,float,float,float]] = None
    last_mind: Optional[Tuple[float,float,float,float]] = None  # (IA,IR,RA,RR)

    def resist_to_quality(r_ohm: Optional[float], max_ohm: float = 2500.0) -> float:
        if r_ohm is None or not math.isfinite(r_ohm) or r_ohm <= 0: return 0.0
        return max(0.0, min(1.0, 1.0 - (float(r_ohm) / max_ohm)))
    q_smooth: Dict[str, float] = {}

    def _norm01(x: Optional[float]) -> Optional[float]:
        if x is None: return None
        fx = float(x)
        return max(0.0, min(1.0, fx/100.0 if fx > 1.5 else fx))

    # --------- emit helpers ---------
    def _emit_spectral(specs: List[Any], ts: float):
        nonlocal last_bands
        for sp in specs:
            def g(k):
                try: return getattr(sp, k)
                except Exception:
                    return sp.get(k) if isinstance(sp, dict) else None
            delta = _norm01(g("Delta")); theta = _norm01(g("Theta")); alpha = _norm01(g("Alpha"))
            beta  = _norm01(g("Beta"));  gamma = _norm01(g("Gamma"))
            if None in (delta, theta, alpha, beta, gamma): continue
            last_bands = (delta, theta, alpha, beta, gamma)
            _print_json("BANDS", {"ts": round(ts, 3), "delta": round(delta,6), "theta": round(theta,6),
                                  "alpha": round(alpha,6), "beta": round(beta,6), "gamma": round(gamma,6)})
            for name, val in (("Delta",delta),("Theta",theta),("Alpha",alpha),("Beta",beta),("Gamma",gamma)):
                _send_num(osc, "BANDS", name, val)
            _send_root(osc, "Delta", delta); _send_root(osc, "Theta", theta); _send_root(osc, "Alpha", alpha)
            _send_root(osc, "Beta", beta);   _send_root(osc, "Gamma", gamma)

    def _emit_mind(minds: List[Any], ts: float):
        nonlocal last_mind
        for md in minds:
            def g(k):
                try: return getattr(md, k)
                except Exception:
                    return md.get(k) if isinstance(md, dict) else None
            inst_att = _norm01(g("Inst_Attention"))
            inst_rel = _norm01(g("Inst_Relaxation"))
            rel_att  = _norm01(g("Rel_Attention"))
            rel_rel  = _norm01(g("Rel_Relaxation"))
            if None in (inst_att, inst_rel, rel_att, rel_rel): continue
            last_mind = (inst_att, inst_rel, rel_att, rel_rel)
            _print_json("MENTAL", {"ts": round(ts,3),
                                   "Inst_Attention": round(inst_att,6),
                                   "Inst_Relaxation": round(inst_rel,6),
                                   "Rel_Attention": round(rel_att,6),
                                   "Rel_Relaxation": round(rel_rel,6)})
            _send_num(osc, "MENTAL", "Inst_Attention", inst_att)
            _send_num(osc, "MENTAL", "Inst_Relaxation", inst_rel)
            _send_num(osc, "MENTAL", "Rel_Attention",  rel_att)
            _send_num(osc, "MENTAL", "Rel_Relaxation", rel_rel)
            _send_root(osc, "Inst_Attention", inst_att)
            _send_root(osc, "Inst_Relaxation", inst_rel)
            _send_root(osc, "Rel_Attention",  rel_att)
            _send_root(osc, "Rel_Relaxation", rel_rel)

    # ---------- callbacks ----------
    def on_state(s, state):
        _print_json("STATE", {"state": _enum_name(state)})
        _status("Sensor state:", _enum_name(state))

    def on_battery(s, pct):
        _print_json("BATTERY", {"percent": pct})
        _send_num(osc, "BATTERY", "percent", pct)
        _pretty_line("BATTERY", f"Battery level is {pct}%")

    def on_resist(s, data):
        ts = time.time()
        max_ohm = 2500.0
        for pkt in _iter(data):
            row = {"ts": round(ts, 3), "pack": _safe(pkt, "PackNum"),
                   "O1": _safe(pkt, "O1"), "O2": _safe(pkt, "O2"),
                   "T3": _safe(pkt, "T3"), "T4": _safe(pkt, "T4"),
                   "units": "Ohm"}
            _print_json("RESIST", row)
            _status("Resist packet", _format_values(pack=row["pack"], O1=row["O1"], O2=row["O2"], T3=row["T3"], T4=row["T4"]))
            if any(row[ch] is None for ch in ("O1", "O2", "T3", "T4")):
                _warn("Missing electrode values in resist packet. Check sensor contact and electrodes.")
            _send_num(osc, "RESIST", "ts", row["ts"]); _send_num(osc, "RESIST", "pack", row["pack"])
            for ch in ("O1","O2","T3","T4"):
                _send_num(osc, "RESIST", ch, row[ch])
                q = resist_to_quality(row[ch], max_ohm=max_ohm)
                q_prev = q_smooth.get(ch, q); q_s = 0.2*q + 0.8*q_prev
                q_smooth[ch] = q_s
                _send_num(osc, "QUALITY", ch, q_s)
            quality_row = {ch: round(q_smooth.get(ch,0.0), 3) for ch in ("O1","O2","T3","T4")}
            _print_json("QUALITY", quality_row)
            _pretty_line("QUALITY", _format_values(**quality_row))

    detrenders: Dict[str, DCDetrender] = {}
    notchers: Dict[str, Optional[NotchIIR]] = {}

    def on_signal(s, data):
        nonlocal calib_started, calib_finished, calib_forced, calib_start_time
        nonlocal last_prog_time, last_prog_value, art_on, art_start
        now = time.time()

        raw_channels = []
        pretty_vals = []

        for pkt in _iter(data):
            o1 = _safe(pkt, "O1"); o2 = _safe(pkt, "O2"); t3 = _safe(pkt, "T3"); t4 = _safe(pkt, "T4")
            if (o1 is None) or (o2 is None) or (t3 is None) or (t4 is None):
                _debug("Signal packet skipped because one or more channels are missing:", _format_values(
                    pack=_safe(pkt, "PackNum"), O1=o1, O2=o2, T3=t3, T4=t4))
                continue

            # emotions lib input (RAW bipolar)
            left_bip  = float(t3) - float(o1)
            right_bip = float(t4) - float(o2)
            raw_channels.append(support_classes.RawChannels(left_bip, right_bip))

            # pretty EEG for console/OSC
            vals = {"O1": float(o1) * _scale, "O2": float(o2) * _scale,
                    "T3": float(t3) * _scale, "T4": float(t4) * _scale}
            for ch in vals:
                v = detrenders[ch].step(vals[ch])
                if notchers[ch]: v = notchers[ch].step(v)
                vals[ch] = v
            pretty_vals.append(vals)

        # EEG stream (prints + OSC)
        for vals in pretty_vals:
            row = {"ts": round(now, 3),
                   "O1": round(vals["O1"], prec), "O2": round(vals["O2"], prec),
                   "T3": round(vals["T3"], prec), "T4": round(vals["T4"], prec),
                   "units": scale_name}
            _print_json("EEG", row)
            for ch in ("O1","O2","T3","T4"):
                _send_num(osc, "EEG", ch, row[ch])

        _debug("Signal callback received", len(raw_channels), "valid channel frames,", len(pretty_vals), "formatted output rows")
        if not raw_channels:
            _warn("No valid EEG frames were produced from the raw signal packets.")
            return

        # Start calibration once (non-blocking)
        if not calib_started:
            _status("Starting emotion calibration.")
            math_lib.start_calibration()
            calib_started = True
            calib_start_time = now
            last_prog_time = now
            last_prog_value = 0.0
            _print_json("CALIB", {"event": "START", "target_sec": int(args.calibration_sec)})
            _send_num(osc, "CALIB", "Started", 1.0)

        # Process emotions
        _debug("Pushing", len(raw_channels), "raw bipolar frames into EmotionalMath")
        math_lib.push_bipolars(raw_channels)
        math_lib.process_data_arr()

        # Artifacts flags (always)
        both_art = 1.0 if getattr(math_lib, "is_both_sides_artifacted", lambda: False)() else 0.0
        seq_art  = 1.0 if getattr(math_lib, "is_artifacted_sequence",  lambda: False)() else 0.0
        _send_num(osc, "ARTIFACT", "Both", both_art)
        _send_num(osc, "ARTIFACT", "Seq",  seq_art)
        if both_art or seq_art:
            _print_json("ARTIFACT", {"both_now": int(both_art), "sequence": int(seq_art)})
            if not art_on:
                art_on = True; art_start = now
        else:
            art_on = False

        # Calibration progress and watchdog
        try:
            if not calib_finished:
                prog = math_lib.get_calibration_percents()
                if prog is not None:
                    p = float(prog)
                    if p > last_prog_value + 0.25:
                        last_prog_value = p; last_prog_time = now
                    p01 = max(0.0, min(1.0, p/100.0))
                    _print_json("CALIB", {"progress_percent": p})
                    _send_num(osc, "CALIB", "Progress", p01)
                if math_lib.calibration_finished():
                    calib_finished = True
                    _print_json("CALIB", {"event": "FINISHED"})
                    _send_num(osc, "CALIB", "Finished", 1.0)
        except Exception:
            pass

        if calib_started and not calib_finished:
            reason = None
            if (now - calib_start_time) >= float(args.calib_max_sec):
                reason = "timeout"
            elif (now - last_prog_time) >= float(args.calib_stall_sec):
                reason = "stall"
            elif args.force_on_artifacts and art_on and ((now - art_start) >= float(args.art_streak_sec)):
                reason = "artifact_streak"
            if reason:
                calib_finished = True; calib_forced = True
                _print_json("CALIB", {"event": "FORCED_FINISH", "reason": reason, "last_progress_percent": round(last_prog_value,2)})
                _send_num(osc, "CALIB", "Finished", 1.0); _send_num(osc, "CALIB", "Forced", 1.0)

        # ----- EMIT ONLY AFTER CALIBRATION (natural or forced) -----
        if not calib_finished:
            return

        # Spectral
        specs = []
        try:
            specs = math_lib.read_spectral_data_percents_arr()
        except Exception:
            specs = []
        if specs:
            _print_json("BANDS_COUNT", {"n": len(specs)})
            _emit_spectral(specs, now)

        # MindData
        minds = []
        try:
            minds = math_lib.read_mental_data_arr()
        except Exception:
            minds = []
        if minds:
            _print_json("MENTAL_COUNT", {"n": len(minds)})
            _emit_mind(minds, now)

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

    # wire callbacks
    sensor.sensorStateChanged   = on_state
    sensor.batteryChanged       = on_battery
    if sensor.is_supported_feature(SensorFeature.Resist): sensor.resistDataReceived = on_resist
    if sensor.is_supported_feature(SensorFeature.FPG):    sensor.fpgDataReceived    = on_fpg
    if sensor.is_supported_feature(SensorFeature.MEMS):   sensor.memsDataReceived   = on_mems
    if sensor.is_supported_feature(SensorFeature.Signal): sensor.signalDataReceived  = on_signal

    # init smoothing filters after callbacks wired
    for ch in ("O1","O2","T3","T4"):
        detrenders[ch] = DCDetrender(alpha=args.detrend_alpha)
        notchers[ch]   = NotchIIR(fs_hz, args.mains_hz, Q=30.0) if args.mains_hz in (50,60) else None

    # device info (immediate)
    _print_json("DEVICE", {
        "family": _enum_name(_safe(sensor, "sens_family")),
        "name": _safe(sensor, "name"),
        "address": _safe(sensor, "address"),
        "serial_number": _safe(sensor, "serial_number"),
        "fs_hz": fs_hz, "process_win_freq_hz": int(args.process_win_freq), "fft_window_samples": int(args.fft_window_samples),
        "scale": scale_name
    })

    # stage runner
    def _run_stage(cmd_start: SensorCommand, cmd_stop: SensorCommand, seconds: int, label: str):
        if seconds <= 0: return
        if not sensor.is_supported_command(cmd_start):
            print(f"# {label}: command not supported, skipping.", flush=True); return
        print(f"# {label}: START ({seconds}s)", flush=True)
        _debug(f"Executing command {cmd_start} for {label}")
        sensor.exec_command(cmd_start)
        t_end = time.time() + seconds
        while time.time() < t_end and not stop_event.is_set():
            time.sleep(0.05)
        sensor.exec_command(cmd_stop)
        _debug(f"Stopping command {cmd_stop} for {label}")
        print(f"# {label}: STOP", flush=True)

    try:
        if not args.no_resist and sensor.is_supported_feature(SensorFeature.Resist):
            _run_stage(SensorCommand.StartResist, SensorCommand.StopResist, args.resist_seconds, "RESIST")
        if not args.no_fpg and sensor.is_supported_feature(SensorFeature.FPG):
            _run_stage(SensorCommand.StartFPG, SensorCommand.StopFPG, args.fpg_seconds, "FPG")
        if not args.no_mems and sensor.is_supported_feature(SensorFeature.MEMS):
            _run_stage(SensorCommand.StartMEMS, SensorCommand.StopMEMS, args.mems_seconds, "MEMS")
        if sensor.is_supported_command(SensorCommand.StartSignal):
            dur = args.signal_seconds
            print("# EEG: START (Ctrl+C to stop)" if dur == 0 else f"# EEG: START ({dur}s)", flush=True)
            sensor.exec_command(SensorCommand.StartSignal)
            t_end = (time.time() + dur) if dur > 0 else None
            while not stop_event.is_set() and (t_end is None or time.time() < t_end):
                time.sleep(0.05)
            sensor.exec_command(SensorCommand.StopSignal)
            print("# EEG: STOP", flush=True)
        else:
            print("# EEG: command not supported, skipping.", flush=True)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"# ERROR during streaming: {e}", flush=True)
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
        print("# Disconnected.", flush=True)


if __name__ == "__main__":
    main()
