# BrainBit Study Runner Debugging

This guide explains how BrainBit is connected to Study Runner and how to debug
the most common lab setup problems.

For the full CLI output reference, see `OUTPUT_REFERENCE.md`. For the longer
standalone CLI documentation, see `README_ENHANCED.md`.

## How BrainBit Connects

Study Runner does not talk to the BrainBit headset directly from the browser.
The backend starts this local CLI process:

```text
software/study_runner/plugins/brainbit/brainbit_realtime_cli.py
```

That CLI uses NeuroSDK over Bluetooth LE to scan for BrainBit-family sensors,
connects to the configured target band when one is set, and prints sensor events
as JSON lines. The Study Runner adapter reads those lines, updates dashboard
status, writes log files, mirrors data to LSL when enabled, and forwards selected
values to TouchDesigner through OSC only during active stimulus phases.

## Important Settings

BrainBit settings live in:

```text
software/study_content/settings/hardware_settings.json
```

Important fields:

- `brainbit.enabled`: enables the integration.
- `brainbit.scan_seconds`: how long one BLE scan attempt runs.
- `brainbit.serial_number`: preferred target band identity.
- `brainbit.device_address`: fallback target address when no serial is known.
- `brainbit.device_name`: optional name target when serial/address are not known.
- `brainbit.device_index`: scan list index fallback; only use this in stable lab setups.
- `brainbit.resist_seconds`: how long electrode resistance is measured before EEG.
- `brainbit.signal_seconds`: EEG duration; `0` means run until stopped.
- `brainbit.python_executable.windows`: optional. Packaged builds run the CLI
  through their own executable (`--brainbit-cli`), so leave this empty unless a
  specific interpreter must be used.
- `brainbit.lsl.stream_prefix`: naming prefix for the mandatory canonical LSL bridge.
- `brainbit.osc_host` and `brainbit.osc_port`: TouchDesigner OSC target.

## Dashboard Debug Flow

1. Start Study Runner and open `/admin`.
2. Enable BrainBit in the sensor dashboard.
3. Click `Start` or `Restart`.
4. Watch the BrainBit status card.

Useful status values:

- `disabled`: integration is off in hardware settings.
- `waiting`: enabled, but no current process/status yet.
- `scanning`: the CLI is scanning for a BrainBit device.
- `connected`: data was received recently.
- `stale`: the process is still known, but no data arrived within the timeout.
- `failed`: the CLI could not start or crashed. The card then names the cause in
  plain language (headset not found, Bluetooth off, libraries missing).
- `restarting`: the CLI exited and is being retried automatically.
- `not_configured`: no way to launch the CLI on this installation.

Scanning happens once per start, but a CLI that exits because the headset was
off is now retried automatically (after 5 s, 15 s, then 45 s, up to
`auto_restart_max_attempts`, default 3). So switching the headset on shortly
after the server starts is usually enough; otherwise click `Restart`.

## Connect a Different Band

Use the dashboard when another BrainBit band should be used:

1. Turn on the desired band and keep other BrainBit bands off when possible.
2. Open `/admin` and click BrainBit `Restart`.
3. Wait for scan candidates in the BrainBit card.
4. Click `Use this band` on the correct candidate.
5. Study Runner saves serial/address/name to `hardware_settings.json` and
   restarts BrainBit.

Target priority is:

1. `serial_number`
2. `device_address`
3. `device_name`
4. `device_index`

If no target is configured, the CLI connects to the first BrainBit it finds.

If a configured serial, address, or name is **not** found, the CLI does not give
up: it connects to the headset that is actually in range and reports the swap
(`DEVICE_TARGET_MISSING`), which the dashboard shows as "The BrainBit saved in
the settings was not found. Using the headset that is in range instead." This
changed in 0.5 - before that the CLI exited, which meant a saved headset from
another computer silently blocked every session.

Exit codes the adapter reacts to:

| Code | Meaning | Retried automatically |
|---|---|---|
| 0 | clean stop | no |
| 2 | SDK libraries missing and not installable | no |
| 5 | no BrainBit found during the scan | yes |
| 103 | Bluetooth off or unavailable | no |
| other | unexpected crash | yes |

## Log Files

Default log location:

```text
software/study_runner/plugins/brainbit/logs/
```

Key files:

- `brainbit_runtime.log`: raw CLI output and SDK messages.
- `brainbit_state.json`: latest parsed status for the dashboard.

When the dashboard status is unclear, check `brainbit_runtime.log` first. When
the dashboard looks stale, check `brainbit_state.json` and the file timestamp.

## Manual CLI Check

Run this from the Study Runner app root:

```powershell
cd software
python study_runner\plugins\brainbit\brainbit_realtime_cli.py --scan-seconds 10 --resist-seconds 10 --signal-seconds 0 --pretty --debug --no-osc
```

Expected flow:

1. The script reports installed dependencies or missing imports.
2. It scans for BrainBit-family sensors.
3. It prints `SCAN`, `DEVICE_SELECTED`, and `DEVICE` lines.
4. It prints `RESIST` and `QUALITY` during electrode checks.
5. It prints `EEG`, `BANDS`, `MENTAL`, `BATTERY`, and calibration/status lines.

Stop with `Ctrl+C`.

## TouchDesigner OSC Check

Use this when the headset works but TouchDesigner does not react:

```powershell
cd software
python study_runner\plugins\brainbit\brainbit_realtime_cli.py --scan-seconds 10 --resist-seconds 10 --signal-seconds 0 --pretty --debug --osc-host 127.0.0.1 --osc-port 8000
```

Then check:

- TouchDesigner project `HelloEEG_HelloMYO_01.3.toe` is open.
- TouchDesigner listens on the same host and port.
- Windows firewall is not blocking UDP.
- The stimulus card has `brainbit_to_touchdesigner` enabled.

In Study Runner, TouchDesigner forwarding is intentionally stimulus-gated. It is
not expected to send OSC continuously outside the active stimulus phase.

## LSL and XDF Check

BrainBit declares `recording_source`, a 250 Hz primary EEG stream, and its
backup projection in `manifest.json`. Canonical LSL publication cannot be
disabled independently. Before the first source recording on a computer, run:

```text
python tools/setup_recording_worker.py
```

BrainBit LSL output is continuous while the CLI and outlets are running. It is
not gated by stimulus activity. The detached Python worker resolves the stable
manifest source IDs and writes BrainBit's own append-never XDF segments; XDF is
internal infrastructure and has no plugin toggle or menu entry.

If LSL streams are missing:

- Install dependencies with `pip install -r software/requirements.txt`.
- Check that `pylsl` imports in the same Python environment.
- Check Recording infrastructure readiness in the dashboard.
- Restart Study Runner after changing hardware settings.
- Confirm the BrainBit dashboard status is `connected`.

## Common Problems

### No compatible BrainBit-family sensor found

Check:

- Bluetooth is enabled on the computer.
- The headset is powered on and charged.
- The headset is close to the computer.
- The headset is not already connected to another app or computer.
- Increase `brainbit.scan_seconds` and click `Restart`.

### Import or SDK error

Run:

```powershell
cd software
pip install -r requirements.txt
python -c "import neurosdk, pythonosc"
```

If this fails, fix the Python environment before debugging Study Runner.

### Packaged build cannot start BrainBit

Since 0.5 packaged builds run the CLI through their own executable
(`study-runner-server --brainbit-cli ...`), and the SDK libraries are bundled
into the release. No Python installation is needed on the operator's machine.

Releases up to and including 0.4.0 did **not** ship the CLI script at all, so
BrainBit could never connect from a packaged install - the dashboard just showed
`not_configured` with no visible error. If a release behaves that way, it is
older than 0.5; update instead of configuring around it.

To check a packaged build by hand:

```powershell
.\study-runner-server.exe --brainbit-cli --no-osc --scan-seconds 5 --signal-seconds 5
```

Only set `python_executable` if a specific interpreter must be used:

```json
"python_executable": {
  "windows": "C:\\Path\\To\\Python\\python.exe",
  "macos": ""
}
```

Then restart Study Runner and click BrainBit `Restart`.

### Poor EEG quality

Watch `RESIST` and `QUALITY` in the CLI or log. Check:

- O1, O2, T3, and T4 electrodes are connected.
- Skin contact is stable.
- Conductive gel or wet electrodes are applied as required.
- The headset is not moving during calibration.

Mental and band values are only useful after calibration has started and the
signal quality is reasonable.

### Dashboard says stale

This means Study Runner has not received BrainBit output recently.

Check:

- `brainbit_runtime.log` is still growing.
- The headset battery is not empty.
- The CLI process did not exit.
- The headset has not disconnected after moving out of range.

Use `Restart` after fixing the physical or Bluetooth issue.

## Reference Files

- `OUTPUT_REFERENCE.md`: expected JSON, OSC, status, and troubleshooting output.
- `README_ENHANCED.md`: detailed standalone CLI documentation.
- `HelloEEG_HelloMYO_01.3.toe`: TouchDesigner reference project.
- `brainbit_realtime_cli.py`: CLI used by Study Runner.
