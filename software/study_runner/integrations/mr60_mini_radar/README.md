# MR60 Mini-radar Study Runner Debugging

This guide explains how the MR60 mini-radar connects to Study Runner and how to
debug the BLE setup in the lab.

For firmware flashing and the 20-byte BLE packet layout, see
`firmware/README.md`.

## How The Radar Connects

The MR60BHA2 sensor is connected to a Seeed Studio XIAO ESP32C6. The firmware in
`firmware/GP_mmwaveBreath_and_Pulse_02.ino` reads heart, breath, distance, and
phase values from the radar and publishes them over Bluetooth LE.

Study Runner does not connect from the browser. The backend starts the
`mini_radar` integration, scans for the BLE device, subscribes to the notify
characteristic, decodes the 20-byte packets, updates the dashboard, mirrors data
to LSL when enabled, and exports compact JSON sidecars with the study results.

Default BLE identity:

- Device name: `MR60_BLE`
- Service UUID: `9d6f0001-7d2a-4c6b-9f4e-5c2b1f4a6e10`
- Notify characteristic UUID: `9d6f0002-7d2a-4c6b-9f4e-5c2b1f4a6e10`
- Notify rate: 10 Hz
- Payload size: 20 bytes, little-endian

## Important Settings

Radar settings live in:

```text
software/study_content/settings/hardware_settings.json
```

Important fields:

- `mini_radar.enabled`: enables the integration.
- `mini_radar.connection_type`: use `ble` for the ESP32C6 firmware.
- `mini_radar.auto_reconnect`: keeps scanning/reconnecting after disconnects.
- `mini_radar.reconnect_delay`: delay between failed scan attempts.
- `mini_radar.data_timeout_seconds`: dashboard stale timeout.
- `mini_radar.lsl.stream_prefix`: naming prefix for the mandatory canonical LSL bridge.
- `mini_radar.ble.device_name`: BLE advertised name to scan for.
- `mini_radar.ble.address.windows`: direct BLE address/identifier for Windows.
- `mini_radar.ble.scan_timeout_seconds`: one scan window length.
- `mini_radar.ble.characteristic_uuid`: notify characteristic to subscribe to.

Use a direct BLE address when several devices with the same name are in the
room. Leave it empty when using the default name-based scan.

## Dashboard Debug Flow

1. Flash the ESP32C6 with `firmware/GP_mmwaveBreath_and_Pulse_02.ino`.
2. Start Study Runner and open `/admin`.
3. Enable `MR60 Mini-radar` in the integrations dashboard.
4. Click `Start` or `Restart`.
5. Watch the radar status card.

Useful status values:

- `disabled`: integration is off in hardware settings.
- `configured`: settings were loaded, but the reader is not running yet.
- `starting`: reader thread started.
- `scanning`: backend is scanning for the BLE device.
- `waiting`: device was not found or disconnected; retry is pending.
- `connected`: samples were received recently.
- `no_presence`: samples arrive, but the sensor does not report presence.
- `stale`: no samples arrived within `data_timeout_seconds`.
- `failed`: dependency, BLE, serial, or connection error.
- `stopped`: reader was stopped from the dashboard.

The dashboard also shows the connection type, device label, scan window, last
scan time, last activity time, and latest values.

## Manual BLE Receiver

Use the diagnostic receiver before debugging the whole Study Runner flow:

```powershell
cd software
python study_runner\integrations\mr60_mini_radar\tools\ble_mr60_receiver.py
```

Expected flow:

1. It scans for `MR60_BLE`.
2. It connects to the ESP32C6.
3. It prints decoded JSON samples.
4. Values update around 10 times per second.

Stop with `Ctrl+C`.

Useful variants:

```powershell
python study_runner\integrations\mr60_mini_radar\tools\ble_mr60_receiver.py --scan-timeout 15
python study_runner\integrations\mr60_mini_radar\tools\ble_mr60_receiver.py --address "<BLE address or identifier>"
python study_runner\integrations\mr60_mini_radar\tools\ble_mr60_receiver.py --csv mr60_debug.csv
python study_runner\integrations\mr60_mini_radar\tools\ble_mr60_receiver.py --osc-port 8000
```

The receiver reuses the production adapter constants and packet decoder, so a
packet that decodes here should also decode in Study Runner.

## LSL and XDF Check

MR60 declares its canonical LSL streams, primary vitals stream, and backup
projection in `manifest.json`. BLE itself does not transport LSL; the local
adapter publishes decoded packets through the mandatory host bridge. Before
the first source recording on a computer, run:

```text
python tools/setup_recording_worker.py
```

Radar LSL output is continuous while the BLE reader and outlets are running. It
is not gated by stimulus activity. The stimulus flag
the plugin's manifest-driven card action marks the active phase for Study
Runner state and summaries, but it does not stop continuous LSL output. The
detached Python worker writes append-never MR60 XDF segments using the stable
manifest source IDs; XDF is internal infrastructure, not a plugin toggle.

If LSL streams are missing:

- Install dependencies with `pip install -r software/requirements.txt`.
- Check that `bleak` and `pylsl` import in the same Python environment.
- Check Recording infrastructure readiness in the dashboard.
- Confirm dashboard status is `connected`.
- Restart Study Runner after changing hardware settings.

## Result Sidecars

The radar adapter keeps recent samples in memory and exports interval samples
when Study Runner saves results. The sidecar is written next to the participant
result JSON and has a name ending in:

```text
mr60_signals.json
```

It includes sample count, timestamps, decoded radar values, sequence/drop
information, jitter, and `card_events` so question and stimulus intervals can be
reconstructed.

## Common Problems

### BLE device not found

Check:

- ESP32C6 is powered and the firmware is running.
- Serial monitor prints `BLE advertising`.
- Device name in firmware matches `mini_radar.ble.device_name`.
- Bluetooth is enabled on the computer.
- No other app is already connected to the ESP32C6.
- Increase `mini_radar.ble.scan_timeout_seconds` and click `Restart`.

### Several MR60 devices are nearby

Set a direct BLE address or identifier:

```json
"address": {
  "windows": "<device identifier>",
  "macos": "",
  "linux": "",
  "default": ""
}
```

Use the manual receiver or OS Bluetooth tooling to identify the right device.

### Connected but no changing values

Check:

- The radar cable and UART connection to the ESP32C6 are correct.
- The person is within sensor range and in front of the radar.
- Wait for the firmware stabilization time.
- Dashboard `latest.present`, `valid`, and `stabilized` values.
- Serial monitor debug output from the ESP32C6.

### Packets ignored or values are null

Study Runner expects exactly the packet documented in `firmware/README.md`.
Missing sensor values are encoded as `-32768` and become `null` in JSON.

If packets are ignored:

- Confirm the firmware is the version in `firmware/`.
- Confirm the notify characteristic UUID matches config.
- Run `python -m unittest discover software\tests -p test_mr60_mini_radar.py`
  to validate the decoder behavior.

### Dashboard says stale

This means the adapter has not received samples recently.

Check:

- ESP32C6 is still powered.
- BLE connection did not drop after moving out of range.
- `auto_reconnect` is enabled.
- Click `Restart` after fixing the physical or Bluetooth issue.

### Different radar or firmware

Config is enough when only the BLE name, address, scan timeout, or UUID changes.
The adapter must be changed when the packet layout or value scaling changes.

For a different 20-byte packet format, update both:

- `adapter.py`: BLE packet decoder and constants.
- `tools/ble_mr60_receiver.py`: it imports the same decoder, so it should follow
  automatically.

Then add or update decoder tests in `software/tests/test_mr60_mini_radar.py`.

## Reference Files

- `firmware/README.md`: firmware setup and BLE packet layout.
- `firmware/GP_mmwaveBreath_and_Pulse_02.ino`: ESP32C6 firmware.
- `tools/ble_mr60_receiver.py`: manual BLE diagnostic receiver.
- `adapter.py`: production packet decoder, BLE reader, LSL mirror, and sidecar export.
- `plugin.py`: Study Runner integration registration and dashboard actions.
