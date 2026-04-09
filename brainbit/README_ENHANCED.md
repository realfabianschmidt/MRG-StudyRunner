# BrainBit Realtime CLI + OSC + Emotions (Enhanced Version)

## Overview
Enhanced Python CLI for BrainBit 2 biosignal headband with real-time EEG streaming, emotional state estimation, and OSC integration.

**New Features:**
- ✅ Automatic library dependency checking and installation
- ✅ Pretty-printed terminal output (`--pretty` flag)
- ✅ Detailed debug event nodes (`--debug` flag)
- ✅ Clear sensor configuration reporting
- ✅ Contact quality feedback for electrode troubleshooting

## Requirements

### Python Packages (Auto-Installed)
- `pyneurosdk2` — BrainBit hardware SDK
- `python-osc` — OSC protocol implementation
- `pyem-st-artifacts` — EmotionalMath signal processing library

### Hardware
- BrainBit 2 headband (LEBrainBit2)
- Bluetooth adapter (Windows/Mac/Linux)
- 4 electrode headset

### OS & Python
- Python 3.7+
- Windows, macOS, or Linux
- Bluetooth/BLE support

## Quick Start

### 1. Run with Auto-Install
```bash
python3 brainbit_realtime_cli_OSC_15.py --osc-port 8000 --pretty
```
**What it does:**
- Checks if required libraries are installed
- Auto-installs any missing packages via pip
- Starts scanning for BrainBit devices over Bluetooth
- Connects to the first found device
- Runs Resist measurement (6s) → EEG streaming (until Ctrl+C)
- Sends OSC messages to TouchDesigner on 127.0.0.1:8000

### 2. Enable Debug Mode
```bash
python3 brainbit_realtime_cli_OSC_15.py --osc-port 8000 --pretty --debug
```
**Shows:**
- Data flow at each processing stage
- Calibration progress and watchdog events
- Signal packet validation details
- Artifact detection triggers
- Electrode contact quality

### 3. Run Extended Measurements
```bash
python3 brainbit_realtime_cli_OSC_15.py \
  --scan-seconds 5 \
  --resist-seconds 10 \
  --fpg-seconds 5 \
  --mems-seconds 5 \
  --signal-seconds 0 \
  --osc-port 8000 \
  --pretty --debug
```
- `--signal-seconds 0` = EEG runs until Ctrl+C
- `--scan-seconds N` = Scan for N seconds
- `--resist-seconds N` = Resistance/impedance measurement for N seconds
- `--fpg-seconds N` = Photoplethysmography (PPG) for N seconds
- `--mems-seconds N` = Accelerometer/gyroscope for N seconds

## Output Modes

### Standard JSON Output (Always)
```
SCAN {"index": 0, "name": "BrainBit", "family": "LEBrainBit2", "address": "DA:AC:A1:99:7B:BA", "serial": "04030072"}
EMO_INIT {"fs_hz": 250, "process_win_freq_hz": 25, ...}
DEVICE {"family": "LEBrainBit2", "name": "BrainBit", ...}
BATTERY {"percent": 60}
RESIST {"ts": 1234567890.123, "pack": 0, "O1": 1500, "O2": 2000, "T3": 1800, "T4": 1600}
QUALITY {"O1": 0.4, "O2": 0.2, "T3": 0.28, "T4": 0.36}
EEG {"ts": 1234567890.123, "O1": 52.3, "O2": -18.5, "T3": 15.2, "T4": 8.9, "units": "uV"}
BANDS {"ts": 1234567890.456, "delta": 0.123, "theta": 0.456, ...}
MENTAL {"ts": 1234567890.456, "Inst_Attention": 0.72, "Rel_Attention": 0.68, ...}
```

### Pretty Output (with `--pretty`)
```
[STATUS] Pretty output=True, Debug mode=False
[STATUS] Connected sensor info:
[STATUS]   family: LEBrainBit2
[STATUS]   name: BrainBit
[STATUS]   address: DA:AC:A1:99:7B:BA
[STATUS]   serial: 04030072
[STATUS]   sampling_frequency: SensorSamplingFrequency((250,))
[STATUS]   battery: 60
[STATUS]   features: Signal, Resist, FPG, MEMS
[STATUS]   commands: StartSignal, StopSignal, StartResist, StopResist, ...
[STATUS] Starting emotion calibration.
[BATTERY] Battery level is 60%
[QUALITY] O1=0.4, O2=0.2, T3=0.28, T4=0.36
```

### Debug Output (with `--debug`)
```
[DEBUG] Sensor support: features= [...] commands= [...]
[DEBUG] Signal callback received 10 valid channel frames, 10 formatted output rows
[DEBUG] Pushing 10 raw bipolar frames into EmotionalMath
[DEBUG] Signal packet skipped because one or more channels are missing: pack=5, O1=None, O2=None, T3=1200, T4=1100
[WARN] No valid EEG frames were produced from the raw signal packets.
[WARN] Missing electrode values in resist packet. Check sensor contact and electrodes.
```

## OSC Message Schema

### EEG Data
```
/BrainBit/EEG/O1 <float>       # Electrode O1 (uV/mV/V)
/BrainBit/EEG/O2 <float>       # Electrode O2
/BrainBit/EEG/T3 <float>       # Electrode T3
/BrainBit/EEG/T4 <float>       # Electrode T4
/BrainBit/O1 <float>           # Direct root-level
```

### Emotional States
```
/BrainBit/MENTAL/Inst_Attention <float>       # Instant attention (0-1)
/BrainBit/MENTAL/Inst_Relaxation <float>      # Instant relaxation (0-1)
/BrainBit/MENTAL/Rel_Attention <float>        # Relative attention
/BrainBit/MENTAL/Rel_Relaxation <float>       # Relative relaxation
```

### Brain Frequency Bands
```
/BrainBit/BANDS/Delta <float>      # 0-4 Hz (deep sleep)
/BrainBit/BANDS/Theta <float>      # 4-8 Hz (drowsiness)
/BrainBit/BANDS/Alpha <float>      # 8-12 Hz (relaxation)
/BrainBit/BANDS/Beta <float>       # 12-30 Hz (active thinking)
/BrainBit/BANDS/Gamma <float>      # 30+ Hz (high cognition)
```

### Electrode Quality
```
/BrainBit/QUALITY/O1 <float>       # 0=poor contact, 1=excellent
/BrainBit/QUALITY/O2 <float>
/BrainBit/QUALITY/T3 <float>
/BrainBit/QUALITY/T4 <float>
```

### Device Status
```
/BrainBit/BATTERY/percent <float>  # 0-100
/BrainBit/ARTIFACT/Both <float>    # 1.0=both sides have noise, 0.0=no artifact
/BrainBit/ARTIFACT/Seq <float>     # 1.0=signal sequence is corrupted
/BrainBit/CALIB/Progress <float>   # 0-1 (calibration %)
/BrainBit/CALIB/Started <float>    # 1.0=calibration started
/BrainBit/CALIB/Finished <float>   # 1.0=calibration complete
/BrainBit/CALIB/Forced <float>     # 1.0=forced early finish (timeout/stall/artifact)
```

## Troubleshooting

### Issue: "No compatible BrainBit-family sensor found"
**Cause:** Bluetooth is off, device is too far, or already connected elsewhere
**Fix:**
1. Ensure Bluetooth is enabled on your computer
2. Power on the BrainBit headband (wear it or place on desk near computer)
3. Wait 2-3 seconds for it to advertise
4. Retry the scan

### Issue: QUALITY values all 0.0, RESIST all null
**Cause:** Poor electrode contact or electrodes not connected
**Fix:**
1. Check all 4 electrodes (O1, O2, T3, T4) are properly seated
2. Apply conductive gel if not pre-applied
3. Ensure skin is clean and moist
4. Re-run with `--pretty --debug` to watch contact quality improve over time

### Issue: EEG signal shows only NaN or 0 values
**Cause:** Calibration not finished, or sensor configuration mismatch
**Fix:**
1. Watch for `CALIB {"event": "... FINISHED"}`  before expecting mental/band data
2. Ensure BrainBit2 amplifier params are correctly set (Gain6, ChModeNormal)
3. Check sampling rate is 250 Hz

### Issue: TouchDesigner not receiving OSC
**Cause:** Wrong IP/port, OSC firewall blocked, or touchdesigner not listening
**Fix:**
```bash
# Verify OSC is working with a simple listener:
python3 -m pythonosc.osc_server 127.0.0.1 8000

# Or run script without --no-osc (should see no errors):
python3 brainbit_realtime_cli_OSC_15.py --osc-port 8000 --pretty
```

### Issue: Script crashes on import
**Cause:** Missing dependencies or Python version mismatch
**Fix:**
```bash
# Script should auto-install, but manual install also works:
pip install pyneurosdk2 python-osc pyem-st-artifacts

# Verify Python version:
python3 --version  # Should be 3.7+
```

## Architecture

### Data Flow
```
BrainBit Headband
  ↓ (Bluetooth @ 250 Hz)
NeuroSDK2 Scanner → Sensor Connection
  ↓
Signal Callbacks:
  - on_signal(data)      → Raw EEG [O1, O2, T3, T4] → Detrending → Notch filter → Bipolar conversion
  - on_resist(data)      → Electrode impedance
  - on_fpg(data)         → Photoplethysmography
  - on_battery(data)     → Battery status
  ↓
EmotionalMath Engine (LibSignalProcessing)
  - Calibration phase (non-blocking, 4-20 sec)
  - FFT spectral analysis → Frequency bands (Delta/Theta/Alpha/Beta/Gamma)
  - Attention/Relaxation estimation
  - Artifact detection
  ↓
Output Channels:
  - JSON stdout (always)
  - OSC UDP (if --osc-port specified)
  - Pretty terminal (if --pretty)
  - Debug nodes (if --debug)
  ↓
TouchDesigner / Other OSC listeners
```

### Calibration Flow
1. **Start:** Signal comes in, calibration triggered (non-blocking)
2. **Progress:** Math engine builds baseline models (progress 0-100%)
3. **Watchdog:** 
   - Timeout if > 20 sec (configurable)
   - Stall if no progress > 8 sec
   - Force-finish if artifact streak > 10 sec
4. **Finish:** Auto or forced, then emit MENTAL/BANDS data every frame

### Electrode Nomenclature
```
    Fp1 Fp2
  AF7   AF8
F7  F3  Fz  F4  F8
A1 (T3) T4  A2
T7  C3  Cz  C4  T8
M1      M2
P7  P3  Pz  P4  P8
      O1  O2
      Iz
```
**BrainBit2 uses:** O1 (left occipital), O2 (right occipital), T3 (left temporal), T4 (right temporal)

## Advanced Options

### Calibration Tuning
```bash
python3 brainbit_realtime_cli_OSC_15.py \
  --calibration-sec 10 \          # Target calibration time (sec)
  --calib-max-sec 25 \            # Max forced finish timeout
  --calib-stall-sec 10 \          # Stall detection (no progress)
  --force-on-artifacts \          # End calib early if artifacts detected
  --art-streak-sec 12 \           # Artifact streak threshold
  --nwins-skip-after-artifact 15  # Windows to skip after artifact
```

### Signal Processing
```bash
python3 brainbit_realtime_cli_OSC_15.py \
  --process-win-freq 25 \         # Processing window freq (Hz)
  --fft-window-samples 1000 \     # FFT bin resolution
  --skip-first-sec 5 \            # Warmup period
  --detrend-alpha 0.02 \          # DC offset removal (0-1)
  --mains-hz 50 \                 # Notch filter (0=off, 50 or 60 Hz)
  --eeg-scale uV \                # Output scale (V, mV, uV)
  --eeg-precision 3               # Decimal places
```

## Integration with TouchDesigner

### Python CHOP to OSC
```glsl
# In TouchDesigner Python CHOP:
import socket
osc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
def onAttend(val):
    msg = f"/td/attention {val}"
    osc_sock.sendto(msg.encode(), ("127.0.0.1", 8001))
```

### Audio Reactivity
Use BrainBit OSC → TD OSC In CHOPs → Movie In FX or SOPs for real-time parameter modulation.

## Performance Notes

### CPU / Network Load
- **Signal processing:** ~2-5% CPU (250 Hz × 4 channels)
- **OSC throughput:** ~50-100 messages/sec (depends on calibration state)
- **Memory:** ~50-80 MB steady state

### Latency
- BrainBit hardware: ~50-100 ms
- Driver/SDK: ~20-50 ms
- Signal processing: ~40-80 ms (1 frame @ 250 Hz + FFT)
- **Total end-to-end:** ~150-250 ms

## License & Credits

**BrainBit SDK:** © Neurotech & affiliated parties
**EmotionalMath:** © Official em_st_artifacts library
**This Script:** Enhanced CLI wrapper with integrated dependency management, debugging, and OSC integration

## Contact & Support

For BrainBit hardware issues: https://neurotech.org/ or manufacturer support
For neurosdk2 / pyneurosdk: GitHub issues on NeuroTech repositories
For script improvements: Edit and extend as needed

---

**Last Updated:** 2026-04-02
**Version:** 15 (Enhanced with auto-setup, debug nodes, pretty output)
