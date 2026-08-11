# MR60BHA2 BLE firmware

Official firmware source for the Study Runner MR60 mini-radar integration.

## Hardware

- Board: Seeed Studio XIAO ESP32C6
- Sensor: Seeed MR60BHA2 mmWave heart/breath sensor
- LED: onboard WS2812 on D1

## Arduino libraries

Install in Arduino IDE:

- Seeed_Arduino_mmWave
- Adafruit NeoPixel
- ESP32 board package with BLE support

Use board `XIAO_ESP32C6` and enable USB CDC on boot for serial debugging.

## Where the code comes from

This sketch is built directly on Seeed's own **`Seeed_Arduino_mmWave`**
library for the MR60BHA2 (`SEEED_MR60BHA2 mmWave; mmWave.begin(); .update();
.getHeartBreathPhases(); .getBreathRate(); .getHeartRate();
.getDistance();`), including UART wiring taken from that library's own
examples. The 20-byte BLE packet format below, and the BPM/status LED logic
around it, are original code for this project, not part of Seeed's library
or examples — Seeed does not publish a BLE relay format of its own.

## BLE interface

- Device name: `MR60_BLE`
- Service UUID: `9d6f0001-7d2a-4c6b-9f4e-5c2b1f4a6e10`
- Notify characteristic UUID: `9d6f0002-7d2a-4c6b-9f4e-5c2b1f4a6e10`
- Notify rate: 10 Hz
- Payload size: 20 bytes, little-endian

## Packet layout

| Offset | Type | Field |
| --- | --- | --- |
| 0 | uint8 | version |
| 1 | uint8 | flags: bit 0 valid, bit 1 stabilized, bit 2 present |
| 2 | uint16 | sequence |
| 4 | uint32 | timestamp_ms |
| 8 | int16 | heartRate_x10 |
| 10 | int16 | breathRate_x10 |
| 12 | int16 | distanceCm_x10 |
| 14 | int16 | heartPhase_x100 |
| 16 | int16 | breathPhase_x100 |
| 18 | int16 | totalPhase_x100 |

Missing values are encoded as `-32768`.

Study Runner receives this stream through
`software/study_runner/plugins/mr60_mini_radar/adapter.py` when
`mini_radar.connection_type` is set to `ble`.
