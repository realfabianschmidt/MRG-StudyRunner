"""
sensor_brainbit.py — BrainBit EEG via Bluetooth for the RPi gateway.

Wraps the existing brainbit_realtime_cli_OSC_15.py subprocess pattern but
runs directly on the RPi. Streams EEG/BANDS/MENTAL data as JSON lines to
stdout for manager.py to consume and forward via LSL.

Usage:
    python sensor_brainbit.py '{"scan_seconds":5,"resist_seconds":6,...}'

Protocol (stdout):
    {"tag":"EEG","channels":{"O1":...,"O2":...,"T3":...,"T4":...},"ts":...}
    {"tag":"BANDS","channels":{"O1":{"delta":...,...},...},"ts":...}
    {"tag":"MENTAL","Inst_Attention":...,"Inst_Relaxation":...,"ts":...}
    {"tag":"BATTERY","percent":85,"ts":...}
    {"tag":"QUALITY","channels":{"O1":3,...},"ts":...}
    {"tag":"ARTIFACT","both_now":false,"sequence":0,"ts":...}
    {"tag":"STATUS","status":"scanning|connecting|connected|error","message":"..."}

This script requires the Neurosity/BrainBit Python SDK (EmotivBCI or similar)
or the repo-local brainbit_realtime_cli. Adapt the inner loop to your SDK.
"""
import json
import subprocess
import sys
import os
import time
from pathlib import Path


def _out(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _status(status: str, message: str = "") -> None:
    _out({"tag": "STATUS", "status": status, "message": message})


def run(config: dict) -> None:
    """
    Launch the existing brainbit CLI script as a subprocess and forward
    its stdout JSON lines. This mirrors exactly how brainbit_adapter.py
    operates on the MacBook, but executed on the RPi instead.
    """
    scan_seconds    = int(config.get("scan_seconds", 5))
    resist_seconds  = int(config.get("resist_seconds", 6))
    signal_seconds  = int(config.get("signal_seconds", 0))
    lsl_stream_prefix = config.get("lsl_stream_prefix", "BrainBit")
    debug           = bool(config.get("debug", False))

    # Locate the brainbit CLI relative to the repo root (one level up from raspi/)
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "brainbit" / "brainbit_realtime_cli_OSC_15.py"

    if not script_path.exists():
        _status("error", f"BrainBit CLI script not found: {script_path}")
        sys.exit(1)

    python = sys.executable
    cmd = [
        python, str(script_path),
        "--scan_seconds", str(scan_seconds),
        "--resist_seconds", str(resist_seconds),
        "--signal_seconds", str(signal_seconds),
        "--lsl_prefix", lsl_stream_prefix,
        "--quiet",
    ]
    if debug:
        cmd.append("--debug")

    _status("starting", f"Launching BrainBit CLI: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(repo_root / "brainbit"),
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        _status("error", f"Failed to start BrainBit CLI: {exc}")
        sys.exit(1)

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                _out(obj)
                continue
        except json.JSONDecodeError:
            pass
        # Non-JSON line (e.g. log message) — emit as status
        _status("info", line)

    proc.wait()
    _status("stopped", f"BrainBit CLI exited with code {proc.returncode}")


if __name__ == "__main__":
    cfg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    run(cfg)
