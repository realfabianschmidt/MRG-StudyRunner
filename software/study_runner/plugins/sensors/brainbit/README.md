# BrainBit — the EEG headband plugin

A BrainBit is a headband with four dry electrodes (T3, T4, O1, O2) that sends
brain signals over Bluetooth 250 times a second. This folder is everything
Study Runner needs to find that headband, hold a connection to it, and turn what
it sends into recorded data.

Nothing outside this folder knows the word "BrainBit". The rest of the
application only ever starts `driver.py` as a separate program and talks to it
through the plugin protocol — which is what makes this folder removable.

There are in fact *two* separate programs at work here, and that is worth
knowing before reading further:

1. Study Runner starts **`driver.py`**, the plugin process.
2. That process starts **`brainbit_realtime_cli.py`**, a second program which is
   the only thing that ever touches the Bluetooth SDK.

The split exists because the vendor SDK takes over the whole Bluetooth stack of
whatever program loads it. Keeping it in its own program means a wedged
Bluetooth stack can be ended and started again without taking Study Runner with
it.

## Files

| File | What it does | In / Out |
|---|---|---|
| `manifest.json` | The plugin's declaration: what it is called, its EEG, bands, mental, quality, battery and diagnostics streams, which settings an operator may change, and the device-selection actions. Read by the core; never executed. | — |
| `driver.py` | Three lines. The program Study Runner actually launches; hands straight over to the shared plugin runtime. | a process launch → a running plugin process |
| `plugin.py` | The plugin's lifecycle: read the settings, start, stop, restart, report status, handle device selection and contact checks, and hand recorded samples back for export. | settings + a command from the dashboard → the adapter doing it |
| `monitor.py` | Connection-scoped facts, battery freshness and bounded graph history. | tagged events → dashboard facts |
| `adapter.py` | The supervisor. Starts and watches the acquisition program, reads its JSON lines, keeps the current picture of what the headband is doing, writes the diagnostic log, and republishes samples to LSL and (during stimuli) to TouchDesigner via OSC. | JSON lines from the CLI → dashboard status, log files, LSL streams |
| `brainbit_realtime_cli.py` | The acquisition program. Owns every vendor-SDK call: scan, connect, measure electrode contact, stream EEG, compute the derived attention/relaxation values, and print one tagged JSON line per event. | Bluetooth → tagged JSON lines on standard output |
| `ui/dashboard.js` | The dashboard panel: connection state, dated contact measurements, live metric graphs and technical details. | plugin status → what the researcher sees |
| `diagnose_backends.py` | A standalone comparison tool (vendor SDK vs. BrainFlow) for when a band behaves oddly. Deliberately *not* part of recording; nothing imports it at runtime. | run by hand → a comparison report |
| `OUTPUT_REFERENCE.md` | The full list of JSON tags the CLI prints and what each field means. | — |
| `README_ENHANCED.md` | Implementation constraints: SDK packet shapes, channel mapping rules, scaling. Read this before changing the decoder. | — |
| `HelloEEG_HelloMYO_01.3.toe` | An example TouchDesigner project showing how to receive the OSC values. Not used by the software. | — |
| `__init__.py` | Marks the folder as a Python package. | — |

## How a connection is made

1. **Scan.** The acquisition program listens for headbands. Every band it hears
   is added to one list, in the order first heard, and announced to the
   dashboard with its position in that list. If a specific band is configured
   (by serial number or address) the scan stops the moment that band
   answers, and keeps listening up to three times the scan window if it has not.
   Without a configured band, the scan runs its full window so the researcher
   can see everything that is around and pick one.
2. **Connect.** A connection attempt that does not take is reported as exactly
   that — not as a crash.
3. **Measure contact.** About six seconds checking how well the electrodes sit.
   No EEG exists yet during this time, and that is normal.
4. **Stream.** EEG flows, and derived values follow once enough signal has been
   seen to calibrate.

If any step fails, the program **waits five seconds and starts again from step
one**, for as long as the plugin is switched on. It does not give up and it does
not need to be restarted by hand. While it is waiting, the dashboard says so and
shows when the next attempt is due.

That last point matters in the room: when the status says *waiting*, pressing
Restart does not help — it interrupts an attempt that is already running.

## Settings

Machine settings live under `brainbit` in the installation's active settings
directory. The settings UI exposes fields declared in `manifest.json`; additional
CLI/adapter options below are advanced configuration fields.

| Setting | What it decides |
|---|---|
| `enabled` | Whether the plugin runs at all. |
| `serial_number` | The band to use. **The most reliable choice** — set this if you have more than one band. |
| `device_address` | The band's Bluetooth address. Used when no serial is known. Separators do not matter: `AA:BB:…`, `AA-BB-…` and bare hex all work. |
| `device_name` | Legacy display hint. A name-only target requires an explicit device selection. |
| `device_index` | Legacy field, ignored for automatic selection because scan order can change. |
| `scan_seconds` | How long one scan listens. A named band is waited for up to three times this long. |
| `resist_seconds` | How long electrode contact is measured before EEG starts. |
| `disconnect_timeout_ms` | How long the band may be silent *while streaming* before it counts as lost. |
| `settle_seconds` | Pause between stopping and starting again, so Bluetooth can let go first. |
| `auto_restart_max_attempts` | How many times the supervisor may restart the acquisition program by itself. Pressing Start or Restart refills this budget. |
| `lsl.stream_prefix` | The name recordings and other tools see. |
| `osc_host` / `osc_port` | Where TouchDesigner is listening. |

## For the operator

**Remove the band from Windows Bluetooth settings.** This is the single most
common cause of a connection that "almost" works. The vendor SDK runs the whole
Bluetooth conversation itself; if Windows has also paired the band, two owners
are competing for one device. Open the Windows Bluetooth page, remove the
BrainBit if it is listed, and let Study Runner connect to it instead. Do not
pair it there again.

Other things worth checking, in order:

- Only one program may own the band. Close the manufacturer's app.
- Switch the band off and on. It advertises itself most eagerly right afterwards.
- Set `serial_number` once, and the guessing stops for good.
- Charge it. A low battery shortens Bluetooth range noticeably.

## Acquisition monitoring and recording

The dashboard separates connection, EEG reception, calibration, battery warnings
and the last electrode-contact measurement. Contact is measured before EEG and
can be checked manually outside an active study. Its timestamp remains visible;
the normalized contact ratio is diagnostic, not a validated quality percentage.

Choose a headset in the device dropdown and press **Connect**. Names are labels;
selection uses serial number or address. A missing saved target is never replaced
by another nearby headset. Without a saved target, a complete scan connects one
unambiguous device or waits for a choice if there are several. **Search again**
disconnects and scans for an explicit new choice. Successful connections are
remembered separately from the configured target.

The selected identity survives dashboard updates, focus changes and reordered
scan results. If it disappears from the list, the dropdown clears instead of
choosing a different headset. While an action is running, the device controls
are disabled; the same controls respect the active study's runtime lock.

Two 60-second graphs show band power and SDK attention/relaxation indices.
They refresh from an at-most-1-Hz preview; full-rate data stays in LSL/XDF.
Each percentage axis covers all valid values in the visible window, with
headroom rounded up in five-percentage-point steps. Low values remain readable
without clipping older peaks. The displayed axis can change as peaks leave the
window; compare values against the labels, not just the height of a line.
Gaps mean unavailable or uncertain data. Derived indices require completed
calibration and carry artifact validity. They are algorithmic outputs, not
independently validated measurements of a participant's mental state.
"Instant" refers to the SDK's current analysis window, not each raw EEG sample;
"relative" refers to calibration. The preview updates at most once per second.

Repeated initialization with unchanged acquisition settings reuses the live
process and LSL outlets. Raw EEG is only scaled to its output unit, never
filtered, rounded or interpolated. Optional analytics errors leave raw EEG
running. Packet discontinuities restart the analytics window and calibration.

`study_runner.brainbit.diagnostics` is an irregular string stream containing
JSON events and a repeated state snapshot. It records contact measurements,
calibration, artifacts, device/algorithm metadata, timing observations and
process exits alongside EEG. A zero process exit code is not a quality score.
Timestamp reconstruction uses a monotone host clock; exact stimulus-to-EEG
latency still requires independent measurement on the actual lab hardware.

The central recording preflight decides whether a study may start. An existing
XDF file alone does not prove EEG was acquired: inspect its streams and sample
counts. Detailed incident evidence and hardware acceptance steps are in
[the implementation report](../../../../../docs/20260915_brainbit.md).

## Falling back to "BrainBit (old)"

The folder next door, `brainbit_old/`, is a frozen copy of this plugin as it was
before the connection work. It exists as a safety net and is switched **off** by
default.

Turn it on only if this plugin fails in a way that stops a study, and then:

- switch **this** plugin off first — the band can only ever be owned by one
  process, so two enabled BrainBit plugins will fight over it and neither will
  work;
- expect its recordings to be labelled `brainbit_old`, so they are not directly
  comparable with existing ones;
- treat it as temporary, and say what happened, so it can be fixed here.

## Tests

- `software/tests/test_brainbit_contract.py` — packet decoding, channel
  mapping, timestamps, "never connect to a different headband than configured".
- `software/tests/test_brainbit_adapter.py` — status derivation, LSL publishing,
  log rotation.
- `software/tests/test_brainbit_launch.py` — how the acquisition program is
  started, and how exit codes become operator messages.
- `software/tests/test_brainbit_reconnect.py` — discovery and reconnection with
  a scripted stand-in for the band: late-appearing bands, absent bands, failed
  connections, the announced-index-is-the-connected-band guarantee, and that
  Bluetooth being switched off is reported once instead of retried forever.
