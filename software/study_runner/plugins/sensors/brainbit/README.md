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
| `manifest.json` | The plugin's declaration: what it is called, which five data streams it produces (EEG, bands, mental, quality, battery), which settings an operator may change, and the "Use this band" button. Read by the core; never executed. | — |
| `driver.py` | Three lines. The program Study Runner actually launches; hands straight over to the shared plugin runtime. | a process launch → a running plugin process |
| `plugin.py` | The plugin's lifecycle: read the settings, start, stop, restart, report status, answer "Use this band", and hand recorded samples back for export. | settings + a command from the dashboard → the adapter doing it |
| `adapter.py` | The supervisor. Starts and watches the acquisition program, reads its JSON lines, keeps the current picture of what the headband is doing, writes the diagnostic log, and republishes samples to LSL and (during stimuli) to TouchDesigner via OSC. | JSON lines from the CLI → dashboard status, log files, LSL streams |
| `brainbit_realtime_cli.py` | The acquisition program. Owns every vendor-SDK call: scan, connect, measure electrode contact, stream EEG, compute the derived attention/relaxation values, and print one tagged JSON line per event. | Bluetooth → tagged JSON lines on standard output |
| `ui/dashboard.js` | The dashboard panel: live signal quality per electrode, the discovered-bands list, and the buttons. | plugin status → what the researcher sees |
| `diagnose_backends.py` | A standalone comparison tool (vendor SDK vs. BrainFlow) for when a band behaves oddly. Deliberately *not* part of recording; nothing imports it at runtime. | run by hand → a comparison report |
| `OUTPUT_REFERENCE.md` | The full list of JSON tags the CLI prints and what each field means. | — |
| `README_ENHANCED.md` | Implementation constraints: SDK packet shapes, channel mapping rules, scaling. Read this before changing the decoder. | — |
| `HelloEEG_HelloMYO_01.3.toe` | An example TouchDesigner project showing how to receive the OSC values. Not used by the software. | — |
| `__init__.py` | Marks the folder as a Python package. | — |

## How a connection is made

This is the part that used to be unreliable, so it is worth stating plainly.

1. **Scan.** The acquisition program listens for headbands. Every band it hears
   is added to one list, in the order first heard, and announced to the
   dashboard with its position in that list. If a specific band is configured
   (by serial number, address or name) the scan stops the moment that band
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

All of these live in `software/study_content/settings/hardware_settings.json`
under `brainbit`, and every one of them is declared in `manifest.json`, so they
can be changed from the settings page rather than by editing the file.

| Setting | What it decides |
|---|---|
| `enabled` | Whether the plugin runs at all. |
| `serial_number` | The band to use. **The most reliable choice** — set this if you have more than one band. |
| `device_address` | The band's Bluetooth address. Used when no serial is known. Separators do not matter: `AA:BB:…`, `AA-BB-…` and bare hex all work. |
| `device_name` | A name to match, when neither of the above is known. |
| `device_index` | Position in the scan list. A last resort: positions can change between scans. |
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

## Known issue: a crash-and-hang report from before the connection rework

Reported 2026-09-15, running a source checkout at a **different, older**
install location (`C:\Users\fabia\MRG-StudyRunner`) than the one this
repository builds from -- one still laid out as `extensions/sensors/brainbit/`,
from before the folder was renamed to `plugins/`, and predating the connection
rework described above (commit `a31b6f4`, "BrainBit: hold a connection instead
of hoping for one"). **Not reproduced on the current code**, and the specific
mechanics below are what the old, one-shot-per-attempt process model made
possible -- not necessarily what the current, persistent-connection model does.

Two separate symptoms, from two attempts in the same session:

1. **First attempt**, with BrainBit and the radar already connected from an
   earlier admin-hub check: pressing Start hung for a long time, then the
   BrainBit driver was terminated, and the participant page reported the study
   could not start.
2. **Second attempt**, connecting fresh: scan, connect and the six-second
   contact measurement all completed cleanly, then `brainbit_realtime_cli.py`
   exited with code `3221225786` about a minute after calibration finished.
   That number is `0xC0000005`, Windows' own name for a native memory access
   violation -- a fault inside the vendor SDK's compiled code, not a Python
   exception, and not something any `try`/`except` in this folder can catch.
   Retrying reproduced the identical crash.

A third thing in the same log is probably unrelated to both: for about 45
minutes, the log repeated "State file write skipped because the file is
locked" -- the status-file writer (write to a temp file, then rename) makes
exactly one attempt per call and gives up silently on a Windows sharing
violation. The log shows this clearing on its own (later connections wrote
normally again), which doesn't line up with either crash, and points more at
something external holding the file open -- a virus scanner, an indexer, a
sync client -- than at anything in this folder's own handle hygiene.

What is verifiably different in the current code, read directly rather than
assumed: the one-shot "scan, connect, stream, exit" process this log implies
is gone. A device that is already connected is reused immediately, not
reconnected. Waiting for the stream contract is bounded (`scan_seconds +
resist_seconds + 20s`, about 31s by default) instead of open-ended. A crash
lands in the same retry path as any other exit code -- up to three attempts
with backoff, then a clear "tried N times, use Restart" failure -- instead of
leaving the connection in an unrecoverable state. What that does *not* mean:
that a native `0xC0000005` fault inside the vendor SDK cannot still happen on
the current architecture. It is a fault in code this project does not own,
and confirming whether it still occurs needs a fresh run on real hardware, not
a reading of the Python around it.

Two specific spots stay open for whoever picks this up next, deliberately
left unchanged here while a larger connection-reliability rework is in
progress: `_set_state` in `adapter.py` has no retry at all on a locked state
file, and `_EXIT_REASONS`/`_CRASH_REASON` file every unrecognized exit code --
including a native access violation -- under the same generic "crashed, will
retry" bucket as ordinary supervision failures, with no separate label for
diagnosis.

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
