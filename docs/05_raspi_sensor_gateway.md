# 05 · Raspberry Pi Sensor Gateway

## Overview

The RPi Gateway moves all sensor hardware onto a Raspberry Pi 4/5 that sits next to the participant. Raw data is streamed wirelessly to the MacBook (study runner), which does all processing, emotion analysis, and recording.

```
RASPBERRY PI                              MACBOOK (study runner :3000)
────────────────────────────────          ────────────────────────────────────
manager.py  ←── HTTP config push ─────── Admin Dashboard (hardware_config.json)
  ├─ sensor_brainbit.py  (subprocess)            ↕ poll every 2 s
  ├─ sensor_emg.py       (subprocess)     /api/raspi/status
  ├─ sensor_radar.py     (subprocess)     /api/raspi/start|stop|restart
  └─ sensor_camera.py    (subprocess)
         │ stdout JSON lines
         ▼
  sender.py  (background thread)
    ├─ LSL outlets ──── WiFi ──────────── LabRecorder (records .xdf)
    └─ camera JPEG ─── HTTP POST ──────── /api/camera/frame → emotion analysis
```

**What runs where:**

| Task | Where |
|------|-------|
| Capture raw sensor data | Raspberry Pi |
| BrainBit Bluetooth pairing | Raspberry Pi |
| EMG / Radar USB serial | Raspberry Pi |
| Webcam capture | Raspberry Pi |
| LSL stream creation | Raspberry Pi |
| Camera emotion analysis | MacBook |
| LSL recording (.xdf) | MacBook (LabRecorder) |
| BrainBit band/signal processing | MacBook |
| All admin dashboard settings | MacBook (browser) |

---

## 1 · Hardware setup

### Required hardware
- Raspberry Pi 4 or 5 (≥2 GB RAM), microSD ≥16 GB
- Power supply ≥3 A
- USB-C webcam (UVC-compatible — works out of the box on Linux)
- BrainBit EEG headset (Bluetooth 4.0+)
- Mini Radar USB dongle (`/dev/ttyUSB0`)
- EMG device USB serial (`/dev/ttyUSB1`, or adapt port in config)
- WiFi on the same LAN as the MacBook

### Wiring
```
RPi USB ports:
  USB-A ──► Webcam
  USB-A ──► Mini Radar dongle  (/dev/ttyUSB0)
  USB-A ──► EMG board           (/dev/ttyUSB1)

Bluetooth (built-in):
  ────────► BrainBit headset
```

### Raspberry Pi OS
- Install **Raspberry Pi OS Lite (64-bit)** (Bookworm, 2024 or later)
- Enable SSH: `sudo raspi-config` → Interface → SSH → Enable
- Connect to WiFi: `sudo raspi-config` → System → Wireless LAN

### Serial port permissions
```bash
sudo usermod -aG dialout $USER
# Then log out and back in
```

---

## 2 · RPi software setup

### Clone the repo
```bash
cd ~
git clone https://github.com/your-org/MRG-StudyRunner.git
cd MRG-StudyRunner
```

### Install Python dependencies
```bash
pip install -r raspi/requirements.txt
```

> **Note:** `opencv-python-headless` may take 5–10 minutes to build on RPi 4.
> On RPi 5, install the pre-built wheel:
> `pip install opencv-python-headless --extra-index-url https://www.piwheels.org/simple`

### Create initial config
The MacBook pushes config automatically when you save settings in the admin dashboard.
To bootstrap manually:

```bash
cp raspi/raspi_config.example.json raspi/raspi_config.json
# Edit IP addresses and sensor ports
nano raspi/raspi_config.json
```

### Run the manager
```bash
cd ~/MRG-StudyRunner/raspi
python manager.py --port 3001
```

### Autostart with systemd
```ini
# /etc/systemd/system/studyrunner-raspi.service
[Unit]
Description=Study Runner RPi Sensor Gateway
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/MRG-StudyRunner/raspi/manager.py --port 3001
WorkingDirectory=/home/pi/MRG-StudyRunner/raspi
Restart=on-failure
RestartSec=5
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable studyrunner-raspi
sudo systemctl start studyrunner-raspi
sudo journalctl -u studyrunner-raspi -f   # live logs
```

---

## 3 · Network requirements

| Port | Direction | Purpose |
|------|-----------|---------|
| 3001 TCP | Mac → RPi | HTTP REST control API |
| 16571–16604 UDP/TCP | RPi → Mac | LSL multicast (pylsl default) |
| 3000 TCP | RPi → Mac | Camera frame POST |

- Both devices **must be on the same LAN** (same WiFi network or wired switch)
- LSL uses multicast — no static routing needed
- If using a static IP on the RPi: `sudo nmcli connection modify "Wired connection 1" ipv4.addresses 192.168.1.100/24 ipv4.method manual`

---

## 4 · Admin dashboard settings

Open `Admin → Settings (⚙) → Hardware Config` on the MacBook.

### `raspi` section

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | bool | Enable the RPi gateway. Set to `true` to show the RPi panel in the dashboard. |
| `host` | string | IP address of the Raspberry Pi (e.g. `"192.168.1.100"`) |
| `port` | int | HTTP port of manager.py on the RPi (default `3001`) |
| `mac_host` | string | IP address of this MacBook as seen from the RPi — needed for camera frame POSTs |
| `mac_port` | int | Port of the study runner on the MacBook (default `3000`) |
| `push_config_on_save` | bool | Automatically push sensor config to RPi whenever hardware config is saved |

### `raspi.sensors.brainbit`

| Key | Description |
|-----|-------------|
| `enabled` | Start BrainBit on RPi |
| `scan_seconds` | Bluetooth scan duration |
| `resist_seconds` | Impedance check duration |
| `lsl_stream_prefix` | LSL stream name prefix (streams: `BrainBit_EEG`, `BrainBit_BANDS`, …) |

### `raspi.sensors.emg`

| Key | Description |
|-----|-------------|
| `enabled` | Start EMG reader |
| `port` | Serial device path (e.g. `/dev/ttyUSB1`) |
| `baudrate` | Serial baud rate (default `115200`) |
| `channel_count` | Number of EMG channels |
| `sample_rate` | Nominal sample rate in Hz (used for LSL stream metadata) |
| `lsl_stream_name` | LSL stream name (default `EMG`) |

### `raspi.sensors.radar`

| Key | Description |
|-----|-------------|
| `enabled` | Start mini radar reader |
| `port` | Serial device path (e.g. `/dev/ttyUSB0`) |
| `baudrate` | Serial baud rate (default `115200`) |
| `auto_reconnect` | Reconnect automatically if serial port disconnects |
| `reconnect_delay` | Seconds to wait before reconnecting |
| `data_timeout_seconds` | Mark sensor as stale after this many seconds without data |
| `lsl_stream_name` | LSL stream name prefix (streams: `MiniRadar_VITALS`, `MiniRadar_PHASES`) |

### `raspi.sensors.camera`

| Key | Description |
|-----|-------------|
| `enabled` | Start webcam capture |
| `device_index` | OpenCV camera index (usually `0` for first USB cam) |
| `width` / `height` | Capture resolution in pixels |
| `fps` | Target frames per second (actual rate depends on USB bandwidth) |
| `jpeg_quality` | JPEG compression quality 1–100 (lower = smaller frames, faster) |

After saving, if `push_config_on_save` is `true`, the new config is pushed to the RPi immediately — no restart required for non-running sensors.

---

## 5 · Data flow

### Per-sensor data path

```
BrainBit (RPi, Bluetooth)
  → sensor_brainbit.py stdout  → manager.py state
  → sender.py LSL outlets       → LabRecorder on Mac (.xdf)

EMG (RPi, USB serial)
  → sensor_emg.py stdout       → manager.py state
  → sender.py LSL outlet        → LabRecorder on Mac (.xdf)

Mini Radar (RPi, USB serial)
  → sensor_radar.py stdout     → manager.py state
  → sender.py LSL outlets       → LabRecorder on Mac (.xdf)

Webcam (RPi, USB)
  → sensor_camera.py           → HTTP POST to Mac :3000/api/camera/frame
  → camera_affect_adapter.py   → emotion analysis
  → LSL CameraEmotion stream    → LabRecorder on Mac (.xdf)
```

### Admin dashboard control loop
```
Admin browser (Mac)
  → GET /api/admin/status (every 2 s)
  → admin_status_service.py calls raspi_adapter.get_status()
  → GET http://raspi:3001/status
  → renders RPi panel with per-sensor live status

Config save
  → POST /api/hardware-config
  → routes.py calls raspi_adapter.push_config(sensors)
  → POST http://raspi:3001/config
  → manager.py hot-reloads, restarts affected sensors
```

---

## 6 · Troubleshooting

### RPi panel shows "unreachable"
- Check that manager.py is running: `sudo systemctl status studyrunner-raspi`
- Check `raspi.host` matches the RPi's actual IP: `hostname -I` on the RPi
- Check firewall: `sudo ufw allow 3001`
- Verify connectivity: `curl http://192.168.1.100:3001/status` from the MacBook terminal

### LSL streams not appearing in LabRecorder
- Both devices must be on the **same LAN** — VPNs or isolated SSIDs block LSL multicast
- Check pylsl version: `python -c "import pylsl; print(pylsl.__version__)"` — should be ≥1.16
- LabRecorder may need to rescan: click "Update" in LabRecorder

### Camera frames not arriving
- Verify `mac_host` in config points to the MacBook's LAN IP (not `127.0.0.1`)
- Test: `curl -X POST http://mac-ip:3000/api/camera/frame -H "Content-Type: application/json" -d '{"image":""}' ` from the RPi
- Check `camera_emotion.enabled` is `true` in the MacBook's hardware config

### Sensor shows "error" or "exited" in dashboard
- Click "↺ Restart" in the RPi panel for that sensor
- Check RPi system logs: `sudo journalctl -u studyrunner-raspi -n 50`
- Verify the USB device path is correct: `ls /dev/ttyUSB*` on the RPi

### BrainBit not connecting
- Ensure BrainBit SDK / CLI (`brainbit/brainbit_realtime_cli_OSC_15.py`) is present in the repo on the RPi
- Increase `scan_seconds` in config if Bluetooth discovery is slow
- Verify Bluetooth is on: `bluetoothctl show`
