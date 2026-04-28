"""
sensor_radar.py — Mini Radar serial reader for the RPi gateway.

Reads pulse & breathing data from a USB serial port and writes JSON lines
to stdout for manager.py to consume.

Usage:
    python sensor_radar.py '{"port":"/dev/ttyUSB0","baudrate":115200,...}'

Protocol (stdout):
    {"tag":"VITALS","heartRate":72.0,"breathRate":16.0,"quality":0.9,
     "distance":null,"heartPhase":null,"breathPhase":null,"totalPhase":null,
     "present":true,"valid":true,"stabilized":true,"ts":1713123456.789}
    {"tag":"STATUS","status":"connected","message":"..."}
"""
import json
import sys
import time


def _out(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _status(status: str, message: str = "") -> None:
    _out({"tag": "STATUS", "status": status, "message": message})


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on", "present"}
    return bool(v)


def run(config: dict) -> None:
    port = config.get("port", "")
    baudrate = int(config.get("baudrate", 115200))
    auto_reconnect = bool(config.get("auto_reconnect", True))
    reconnect_delay = float(config.get("reconnect_delay", 5.0))
    data_timeout = float(config.get("data_timeout_seconds", 5.0))

    if not port:
        _status("error", "No serial port configured.")
        sys.exit(1)

    try:
        import serial
    except ImportError:
        _status("error", "pyserial not installed. Run: pip install pyserial")
        sys.exit(1)

    conn = None
    last_reconnect = 0.0

    _status("starting", f"Radar reader starting on {port}")

    while True:
        if conn is None:
            now = time.time()
            if now - last_reconnect < reconnect_delay:
                time.sleep(0.2)
                continue
            last_reconnect = now
            try:
                conn = serial.Serial(port=port, baudrate=baudrate, timeout=0.2)
                _status("connected", f"Connected to {port}")
            except Exception as exc:
                _status("waiting", f"Connection failed: {exc}")
                conn = None
                continue

        try:
            raw = conn.readline()
        except Exception as exc:
            _status("error", f"Read error: {exc}")
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            if not auto_reconnect:
                break
            continue

        if not raw:
            continue

        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(payload, dict):
            continue

        _out({
            "tag": "VITALS",
            "heartRate":    _to_float(payload.get("heartRate")),
            "breathRate":   _to_float(payload.get("breathRate")),
            "quality":      _to_float(payload.get("quality", payload.get("signalQuality"))),
            "distance":     _to_float(payload.get("distance")),
            "heartPhase":   _to_float(payload.get("heartPhase")),
            "breathPhase":  _to_float(payload.get("breathPhase")),
            "totalPhase":   _to_float(payload.get("totalPhase")),
            "present":      _to_bool(payload.get("present", payload.get("presence", True))),
            "valid":        _to_bool(payload.get("valid", True)),
            "stabilized":   _to_bool(payload.get("stabilized", True)),
            "ts":           time.time(),
        })


if __name__ == "__main__":
    cfg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    run(cfg)
