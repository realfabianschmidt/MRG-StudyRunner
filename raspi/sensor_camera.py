"""
sensor_camera.py — NoIR picamera2 capture for Pi 5.

Captures JPEG frames from the Raspberry Pi CSI camera (OV5647 NoIR) using
picamera2 and forwards them to the Pi Flask server's /api/camera/frame endpoint
for emotion analysis by the remote Emotion Worker (Mac/Windows).

Requires:
    sudo apt install python3-picamera2   (preferred on Pi OS)
    pip install gpiozero                 (for IR LED control)

Usage:
    python sensor_camera.py '{"ir_led_pin":17,"width":640,"height":480,"fps":10,...}'

Config keys:
    ir_led_pin              (int)   BCM GPIO pin for IR LED bank (0 = disabled)
    ir_led_duty_cycle       (float) 0.0–1.0 PWM duty cycle (default 0.8)
    ir_led_max_temp_c       (float) CPU temp °C at which LED shuts off (default 70)
    ir_thermal_check_interval (int) seconds between thermal checks (default 30)
    width                   (int)   capture width  (default 640)
    height                  (int)   capture height (default 480)
    fps                     (float) target capture rate (default 10)
    jpeg_quality            (int)   JPEG quality 1–100 (default 85)
    server_host             (str)   Pi Flask host, default "127.0.0.1"
    server_port             (int)   Pi Flask port, default 3000
    study_id                (str)
    participant_id          (str)

Protocol (stdout JSON lines — identical to old USB-webcam version):
    {"tag":"STATUS","status":"running","message":"..."}
    {"tag":"FRAME","width":640,"height":480,"bytes":12345,"ts":1713123456.789}
    {"tag":"STATUS","status":"warning","message":"..."}
    {"tag":"STATUS","status":"error","message":"..."}
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time
import urllib.error
import urllib.request


def _out(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def _status(status: str, message: str = "") -> None:
    _out({"tag": "STATUS", "status": status, "message": message})


def run(config: dict) -> None:
    # --- Config ----------------------------------------------------------------
    ir_led_pin             = int(config.get("ir_led_pin", 0))
    ir_led_duty_cycle      = float(config.get("ir_led_duty_cycle", 0.8))
    ir_led_max_temp_c      = float(config.get("ir_led_max_temp_c", 70.0))
    ir_thermal_interval    = int(config.get("ir_thermal_check_interval", 30))
    width                  = int(config.get("width", 640))
    height                 = int(config.get("height", 480))
    fps                    = float(config.get("fps", 10.0))
    jpeg_quality           = int(config.get("jpeg_quality", 85))
    server_host            = config.get("server_host", "127.0.0.1")
    server_port            = int(config.get("server_port", 3000))
    study_id               = config.get("study_id", "")
    participant_id         = config.get("participant_id", "")

    url = f"http://{server_host}:{server_port}/api/camera/frame"

    # --- IR LED ----------------------------------------------------------------
    ir_led = None
    if ir_led_pin > 0:
        try:
            from ir_led_controller import IRLEDController
            ir_led = IRLEDController(
                pin=ir_led_pin,
                duty_cycle=ir_led_duty_cycle,
                max_temp_c=ir_led_max_temp_c,
            )
            ir_led.on()
        except ImportError:
            _status("warning", "ir_led_controller not found — IR LED disabled")
        except Exception as exc:
            _status("warning", f"IR LED init failed: {exc}")

    # --- Camera ----------------------------------------------------------------
    try:
        from picamera2 import Picamera2
    except ImportError:
        _status("error", "picamera2 not installed. Run: sudo apt install python3-picamera2")
        if ir_led:
            ir_led.shutdown()
        sys.exit(1)

    try:
        picam2 = Picamera2()
        cam_config = picam2.create_still_configuration(
            main={"size": (width, height), "format": "BGR888"},
            buffer_count=2,
        )
        picam2.configure(cam_config)
        picam2.start()
    except Exception as exc:
        _status("error", f"Camera init failed: {exc}")
        if ir_led:
            ir_led.shutdown()
        sys.exit(1)

    _status("running", f"picamera2 {width}×{height} @ {fps} fps → {url}")

    # --- Capture loop ----------------------------------------------------------
    interval           = 1.0 / max(1.0, fps)
    last_thermal_check = time.time()
    sequence_number    = 0

    try:
        while True:
            t0 = time.time()

            # Periodic thermal check for IR LED
            if ir_led and (t0 - last_thermal_check) >= ir_thermal_interval:
                ir_led.check_thermal()
                last_thermal_check = t0

            # Capture frame as BGR numpy array
            try:
                frame = picam2.capture_array("main")
            except Exception as exc:
                _status("warning", f"Capture failed: {exc}")
                time.sleep(1.0)
                continue

            # Encode to JPEG using cv2 (already in root requirements.txt)
            try:
                import cv2
                ok, buf = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
                )
                if not ok:
                    _status("warning", "JPEG encode failed")
                    continue
                jpg_bytes = buf.tobytes()
            except ImportError:
                # Fallback: use picamera2's own JPEG encode via PIL
                try:
                    from PIL import Image
                    img = Image.fromarray(frame[:, :, ::-1])  # BGR→RGB
                    buf_io = io.BytesIO()
                    img.save(buf_io, format="JPEG", quality=jpeg_quality)
                    jpg_bytes = buf_io.getvalue()
                except Exception as exc2:
                    _status("warning", f"JPEG fallback encode failed: {exc2}")
                    continue

            sequence_number += 1
            captured_at = time.time()
            b64 = base64.b64encode(jpg_bytes).decode("ascii")

            payload = json.dumps({
                "image": b64,
                "image_format": "image/jpeg",
                "width": width,
                "height": height,
                "client_captured_at": captured_at,
                "sequence_number": sequence_number,
                "study_id": study_id,
                "participant_id": participant_id,
                "source": "raspi_picamera2",
                "active_phase": True,
            }).encode()

            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    resp.read()
                _out({"tag": "FRAME", "width": width, "height": height,
                      "bytes": len(jpg_bytes), "ts": captured_at,
                      "seq": sequence_number})
            except (urllib.error.URLError, OSError) as exc:
                _status("warning", f"Frame POST failed: {exc}")

            elapsed = time.time() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        pass
    finally:
        _status("stopped", "Camera subprocess exiting")
        if ir_led:
            ir_led.shutdown()
        try:
            picam2.stop()
        except Exception:
            pass


if __name__ == "__main__":
    cfg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    run(cfg)
