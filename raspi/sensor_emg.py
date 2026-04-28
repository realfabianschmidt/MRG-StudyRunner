"""
sensor_emg.py — EMG / Muscle Tonus reader for the RPi gateway.

Reads electromyography (muscle tension) data from a USB serial device
(e.g. OpenBCI Cyton, MyoWare, or custom ADC board) connected to the RPi.

Usage:
    python sensor_emg.py '{"port":"/dev/ttyUSB1","baudrate":115200,"channel_count":2,...}'

Protocol (stdout):
    {"tag":"EMG","channels":{"ch0":0.423,"ch1":0.187},"ts":1713123456.789}
    {"tag":"STATUS","status":"connected|error|waiting","message":"..."}

Expected serial data format from the device:
    JSON lines: {"ch0": 0.423, "ch1": 0.187}
    Or CSV:     0.423,0.187

Adapt _parse_line() for your specific device protocol.
"""
import json
import sys
import time


def _out(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _status(status: str, message: str = "") -> None:
    _out({"tag": "STATUS", "status": status, "message": message})


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_line(line: str, channel_count: int) -> dict | None:
    """
    Parse one line from the EMG device.
    Supports JSON dict or comma-separated floats.
    Returns a channels dict {ch0: float, ch1: float, ...} or None.
    """
    line = line.strip()
    if not line:
        return None

    # Try JSON first
    try:
        payload = json.loads(line)
        if isinstance(payload, dict):
            return {f"ch{i}": _to_float(payload.get(f"ch{i}", payload.get(str(i)))) for i in range(channel_count)}
        if isinstance(payload, list):
            return {f"ch{i}": _to_float(payload[i]) for i in range(min(channel_count, len(payload)))}
    except json.JSONDecodeError:
        pass

    # Try CSV fallback
    parts = line.split(",")
    if len(parts) >= channel_count:
        return {f"ch{i}": _to_float(parts[i]) for i in range(channel_count)}

    return None


def run(config: dict) -> None:
    port          = config.get("port", "")
    baudrate      = int(config.get("baudrate", 115200))
    channel_count = int(config.get("channel_count", 2))
    auto_reconnect = bool(config.get("auto_reconnect", True))
    reconnect_delay = float(config.get("reconnect_delay", 5.0))

    if not port:
        _status("error", "No serial port configured for EMG.")
        sys.exit(1)

    try:
        import serial
    except ImportError:
        _status("error", "pyserial not installed. Run: pip install pyserial")
        sys.exit(1)

    conn = None
    last_reconnect = 0.0

    _status("starting", f"EMG reader starting on {port} ({channel_count} channels)")

    while True:
        if conn is None:
            now = time.time()
            if now - last_reconnect < reconnect_delay:
                time.sleep(0.2)
                continue
            last_reconnect = now
            try:
                conn = serial.Serial(port=port, baudrate=baudrate, timeout=0.1)
                _status("connected", f"EMG connected on {port}")
            except Exception as exc:
                _status("waiting", f"EMG connection failed: {exc}")
                conn = None
                continue

        try:
            raw = conn.readline()
        except Exception as exc:
            _status("error", f"EMG read error: {exc}")
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
        channels = _parse_line(line, channel_count)
        if channels is None:
            continue

        _out({"tag": "EMG", "channels": channels, "ts": time.time()})


if __name__ == "__main__":
    cfg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    run(cfg)
