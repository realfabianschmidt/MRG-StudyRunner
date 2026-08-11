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
as structured JSON lines. Raw EEG is emitted in timestamped `EEG_BATCH` chunks
instead of one flushed terminal line per sample. The Study Runner adapter reads those lines, updates dashboard
status, writes log files, mirrors data to LSL when enabled, and forwards selected
values to TouchDesigner through OSC only during active stimulus phases.

## Architecture (API v4)

Like every Study Runner plugin, the core process never imports this folder's
Python modules directly — it only ever starts `driver.py` as a subprocess
(see `docs/file-guide.md`). Inside that subprocess:

- `driver.py` — the only executable entry point (`run_plugin_driver("brainbit")`).
- `plugin.py` — status/lifecycle/admin-action wiring (e.g. `select_device`),
  running inside the subprocess.
- `adapter.py` — supervises the `brainbit_realtime_cli.py` process, parses
  its tagged JSON lines, and mirrors raw/derived data to LSL and OSC.
- `brainbit_realtime_cli.py` — the actual NeuroSDK acquisition script (see
  below); this is the piece that owns the vendor SDK calls.
- `diagnose_backends.py` — a standalone NeuroSDK/BrainFlow A/B comparison
  tool, deliberately outside the acquisition path (see the section below).

At startup the CLI now also fails closed if the pinned `pyem-st-artifacts`
wheel does not expose `EmotionalMath.push_bipolars` — before any device scan
or connection begins, not only on the first EEG batch of an already-running
session.

## Important Settings

BrainBit settings live in:

```text
software/study_content/settings/hardware_settings.json
```

Important fields:

- `brainbit.enabled`: enables the integration.
- `brainbit.scan_seconds`: how long one BLE scan attempt runs.
- `brainbit.serial_number`: preferred target band identity.
- `brainbit.device_address`: target address when no serial is known.
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

If a configured serial, address, or name is **not** found, the CLI exits with
code 6 and `DEVICE_TARGET_MISSING`. It deliberately does not substitute another
participant's band: that would create valid-looking data under the wrong device
identity. Clear or change the saved target explicitly from the dashboard.

Exit codes the adapter reacts to:

| Code | Meaning | Retried automatically |
|---|---|---|
| 0 | clean stop | no |
| 2 | SDK libraries missing and not installable | no |
| 5 | no BrainBit found during the scan | yes |
| 6 | configured BrainBit not found; no substitute selected | yes |
| 7 | SDK callback/data-processing failure | yes |
| 8 | stream/configuration failure | yes |
| 103 | Bluetooth off or unavailable | no |
| other | unexpected crash | yes |

## Log Files

Default log location:

```text
software/study_runner/plugins/brainbit/logs/
```

Key files:

- `brainbit_runtime.log`: bounded CLI diagnostics and timestamped raw EEG chunks.
- `brainbit_state.json`: latest parsed status for the dashboard.

The runtime log rotates at 10 MiB by default and retains three numbered backups.

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
5. It prints raw `EEG_BATCH`, derived `BANDS_BATCH` / `MENTAL_BATCH`, `BATTERY`,
   and calibration/status lines.

`EEG_BATCH.samples` contains unit-scaled but otherwise unfiltered values;
`packs` and `markers` retain the SDK packet metadata. Its
`timestamps` are reconstructed at the nominal sampling interval and forwarded
explicitly to LSL. The detrended/notched `preview` is only for OSC and terminal
inspection; it is not the canonical recording signal.

`EEG_BATCH` also carries two QC fields: `measured_hz` (samples emitted
divided by elapsed wall time since the stream started — the actually
achieved rate, not the nominal 250 Hz; compare against
`diagnose_backends.py`'s `effective_wall_rate_hz`) and
`queue_overflow_dropped_total` (how many samples were ever dropped because a
stalled consumer could not keep up with the throttled flush — should stay 0
in a healthy session).

Stop with `Ctrl+C`.

## Built-in Diagnostic Console

The BrainBit dashboard exposes the generic plugin diagnostics modal. It relays
the bounded driver output and complete status without opening an operating-
system shell. Supported BrainBit input commands are:

```text
help, status, health, channels, raw, derived, errors, start, stop, restart
```

`raw` shows the latest exact EEG values, raw resistance in Ohm, and the clearly
labeled diagnostic quality projection. `errors` includes callback, stream, LSL,
log and packet-integrity failures. Console input is locked during a running
study unless the operator explicitly unlocks it with a recorded reason.

## 30-second NeuroSDK / BrainFlow A-B Check

Use this only outside a study. Close Study Runner and the manufacturer app; run
the two backends sequentially because a BLE band cannot be owned twice.

```powershell
cd software
python study_runner\plugins\brainbit\diagnose_backends.py --backend neurosdk --duration 30 --serial-number YOUR_SERIAL --report study_runner\plugins\brainbit\logs\neurosdk-report.json
python -m pip install brainflow
python study_runner\plugins\brainbit\diagnose_backends.py --backend brainflow --duration 30 --serial-number YOUR_SERIAL --report study_runner\plugins\brainbit\logs\brainflow-report.json
python study_runner\plugins\brainbit\diagnose_backends.py --compare study_runner\plugins\brainbit\logs\neurosdk-report.json study_runner\plugins\brainbit\logs\brainflow-report.json
```

The harness reports sample count, effective rate, every channel's min/max/RMS,
constant channels, timestamp ordering, packet gaps and backend errors. A
BrainFlow run is explicit and never becomes a silent production fallback.
BrainFlow currently documents `BoardIds.BRAINBIT_BOARD`, optional
`serial_number`, and Windows 10+/macOS support in its
[official board list](https://github.com/brainflow-dev/brainflow/blob/master/docs/SupportedBoards.rst#brainbit).

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

Mental and band values are only available after native calibration finishes.
The displayed normalized `QUALITY` value is a linear diagnostic projection,
not a manufacturer-validated electrode-quality score; use the raw Ohm values,
open-channel flags and raw EEG together when diagnosing contact.

### Dashboard says stale

This means Study Runner has not received raw BrainBit EEG recently. Battery,
status, or warning lines do not keep raw acquisition healthy.

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
- `diagnose_backends.py`: optional sequential NeuroSDK/BrainFlow raw-data check.
- `driver.py`: thin API-v4 plugin-process entry point; it does not replace the acquisition CLI.
