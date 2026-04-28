# KNOMI 2 Firmware — Flash Guide

## Prerequisites

- ESP-IDF v5.x installed and `$IDF_PATH` set
- BTT KNOMI 2 connected via USB-C to Mac/Windows
- Python ≥ 3.10, `esptool.py` available (included with ESP-IDF)

## 1. Configure WiFi and Pi IP

Edit `sdkconfig.defaults` before building:

```
CONFIG_STUDY_RUNNER_WIFI_SSID="YourNetwork"
CONFIG_STUDY_RUNNER_WIFI_PASSWORD="YourPassword"
CONFIG_STUDY_RUNNER_PI_HOST="192.168.1.100"   # Pi 5 IP on local network
CONFIG_STUDY_RUNNER_PI_PORT=3000
```

Alternatively, configure interactively:
```bash
idf.py menuconfig
# → Component config → Study Runner → WiFi / Pi settings
```

## 2. Add qrcodegen source

The `components/qrcodegen/` directory contains only a CMakeLists.txt.
Download the C implementation from the MIT-licensed qrcodegen library:

```bash
curl -o knomi_firmware/components/qrcodegen/qrcodegen.c \
  https://raw.githubusercontent.com/nayuki/QR-Code-generator/master/c/qrcodegen.c
curl -o knomi_firmware/components/qrcodegen/qrcodegen.h \
  https://raw.githubusercontent.com/nayuki/QR-Code-generator/master/c/qrcodegen.h
```

## 3. Add BTT KNOMI 2 BSP (Board Support Package)

The display (ST7701 or GC9A01) and touch (CST816S) init depend on the BSP.
Clone the BTT KNOMI repo and copy the BSP component:

```bash
git clone https://github.com/bigtreetech/KNOMI.git /tmp/knomi_bsp
cp -r /tmp/knomi_bsp/firmware/KNOMI2/components/bsp knomi_firmware/components/
```

In `main/main.cpp`, uncomment the BSP init lines:
```cpp
bsp_display_start();
bsp_touch_start();
```

## 4. Build

```bash
cd knomi_firmware
idf.py set-target esp32s3
idf.py build
```

## 5. Flash

Put KNOMI 2 into flash mode (hold BOOT button, tap RESET, release BOOT):

```bash
idf.py -p /dev/tty.usbmodem* flash monitor
# On Windows:
idf.py -p COM5 flash monitor
```

## 6. Normal operation

After flashing:
1. Plug KNOMI 2 into Pi 5 via USB-C (power only — data via WiFi)
2. KNOMI 2 connects to WiFi and begins polling `http://PI_IP:3000/api/admin/status`
3. Home screen shows sensor status dots within ~5 seconds
4. Tap any dot → detail page → tap [RESTART] to trigger Pi adapter restart
5. Tap QR icon → network screen → scan QR with iPad or Mac camera to open study runner

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dots all grey | Pi not reachable — check WiFi SSID/password and Pi IP in sdkconfig |
| Black screen | BSP not initialised — check BSP component and uncomment bsp_display_start() |
| QR not visible | URL too long — use a short Pi hostname or IP |
| RESTART has no effect | Check `POST /api/display/action` returns 200 — verify Pi Flask is running |
