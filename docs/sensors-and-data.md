# Sensors And Data

## Active Study

The active study is stored in
`software/study_content/settings/study_config.json`. Saved presets live in
`software/study_content/studies/` as `.study-runner` files.

Plugin choices use this manifest-driven schema. The manifest and process
contract behind it is
[API v5](plugin-recording-architecture.md#manifest-api-v5):

```json
{
  "study_settings": {
    "plugins": {
      "brainbit": {
        "enabled": true,
        "required": true,
        "settings": {}
      }
    }
  }
}
```

Selected sensors are required unless explicitly made optional. Machine-level
settings and temporary runtime overrides do not rewrite the saved study unless
the operator saves it deliberately. Legacy sensor/destination fields are
normalized when a study is loaded.

## Canonical Session Data

New results use one collision-safe session folder:

```text
<configured DATA_DIR>/
  <study>/
    sessions-index.csv
    _work/{partial,flush,recovery}/
    <participant>/
      <YYYYMMDDTHHMMSSZ>__<session-id>/
        answers/
          submission.json
          result.json
          card-summary.json
        meta/
          session-identity.json
          manifest.json
          checksums.sha256
          finalization-state.json
          quality.jsonl
          timing.jsonl
          logs/finalization.jsonl
        raw/plugins/<plugin>/part-0001.xdf
        raw/backup/slowest-grid_<rate>hz.xdf
        derived/session.xdf
        session_1hz.csv
        COMPLETE.json | ATTENTION_REQUIRED.json | WITHDRAWN.json
```

`answers/` holds everything a participant contributed; `meta/` holds
internal/operational state nobody needs to read by hand. `session_1hz.csv`
contains selected synchronized sensor channels and quality fields on the
backup grid (see [plugin-recording-architecture.md](plugin-recording-architecture.md)).
The XDF files retain the higher-resolution recordings. `sessions-index.csv`
is an atomic, rebuildable overview of the study's completed sessions; `_work/`
holds partial checkpoints, periodic flushes and recovery dumps separately.
Status markers (`COMPLETE.json`/`ATTENTION_REQUIRED.json`/`WITHDRAWN.json`)
stay at the session root, same as before.

**Only this flat layout is read.** The session browser and `sessions-index.csv`
never scan the older, nested `<study>/participants/<participant>/sessions/<session>/`
layout from earlier versions, and there is no automatic migration of it on
upgrade. Those older result folders are left untouched on disk and keep
working with the version that wrote them; they simply do not appear in a newer
install's session browser. An operator keeping long-term access to pre-upgrade
results should either read them with the old installation or move each
session folder to the flat layout above by hand before upgrading.

The original pseudonymous participant ID is preserved in JSON. Sanitized path
components, UTC start, and immutable session ID prevent collisions when one
participant repeats a study. The data directory can be outside the installation.
Only the bundled demo result is seeded automatically into a newly configured data directory.

`submission.json` is the atomic local participant commit. `result.json` is the
published result view. `manifest.json` and `checksums.sha256` record provenance
and artifact integrity. `finalization-state.json` plus the JSONL log make every
step and retry replayable after a process restart.

Answers in the canonical submission have passed each Card's validator and may
therefore be normalized (for example, whitespace, numeric types or omitted
optional answers). They are not a byte-for-byte copy of the browser's draft.
An optional Card left unanswered is explicitly listed as skipped. The
versioned `_work/partial/<session>.json` checkpoint is a recovery artifact for
acknowledged Cards; its unfinished Card draft is deliberately excluded and
must be completed again after a reload. Older partial files remain available
for operator recovery but are not silently promoted to verified checkpoints.

## How The Recording Is Produced

You do not have to configure any of this; it follows from the plugins the
study selects. The short version, in the order it happens:

- **Acquisition.** Every recorded sensor and marker stream reaches the app as
  an LSL stream. Network-native sources publish LSL themselves; BLE, serial,
  local hardware and browser sources are republished by a host-side bridge.
  BLE itself is not an LSL transport. Browser samples need HTTPS.
- **Native raw XDF.** Each active plugin gets its own segments at its own
  rate, with raw timestamps kept as recorded. A worker restart opens a new
  segment rather than appending to a possibly damaged one.
- **Slowest-grid backup.** A second, reduced recording samples every plugin on
  one shared grid, chosen at session start as the lowest backup rate any
  active sensor declares. Missing values are never carried forward: they are
  `NaN` with a companion status of `missing`, `valid`, `stale` or `degraded`.
  It is a recovery and QC artifact, not a substitute for the raw data.
- **Merge.** `derived/session.xdf` combines the sources with no resampling, no
  clock synchronization and no dejittering, then is validated against every
  source -- metadata, sample counts, the full timestamp sequence, clock
  offsets and a normalized data hash. A mismatch fails finalization; it never
  becomes a quietly completed session.

Each plugin manifest declares its stable stream and source IDs, channel names,
units, format, nominal rate and clock domain. Marker and clock-diagnostic
streams are hidden recording providers and appear exactly once in the merged
session.

The exact file names, manifest fields and validation rules are in
[plugin-recording-architecture.md](plugin-recording-architecture.md#raw-and-backup-recording).

## Cover Page, Info Cards, And Recording Time

The participant flow is: waiting slide, admin release, optional **cover page**,
Participant ID card, then the study cards.

- The cover page (Study settings, Participant experience) comes before the
  Participant ID. No session, sensor recording, or marker exists yet, so the
  time spent on it is not recorded and never appears in card timing, the
  recording quality window, card summaries, or the CSV export.
- Recording and the `study_start` marker begin, as before, when the
  participant presses Start on the Participant ID card.
- An **info card** is an ordinary card without an answer. It is recorded like
  any other card: `question_shown` and `question_answered` markers bracket the
  reading time, and the card summary gives that interval its own sensor
  window. Put an info card directly after the Participant ID card to get a
  recorded rest or baseline phase.
- Images for info cards and the cover page are stored content-addressed next to
  the studies and travel inside the exported `.study-runner` package
  (`study.json`, `manifest.json` with SHA-256 per file, `assets/`).

## Card Summaries

`card-summary.json` is derived only from the validated merged XDF and marker
windows. Windows are half-open: `[start, end)`. Numeric channels include:

- `count`
- `valid_count`
- `mean`
- `min`
- `max`
- sample `stddev` (`null` below two valid samples)
- expected sample count and coverage for regular streams
- missing/drop count and maximum gap
- time source and plugin status

Boolean values are treated as 0/1, so their mean is the true proportion.
Categorical values contain frequencies and a mode. These values are descriptive
card-level statistics, not a complete EEG, radar, or clinical biosignal
analysis. The merged and source XDF files remain the scientific basis.

## Camera And Emotion

Camera capture and emotion analysis are one `camera_emotion` plugin. Browser
capture and local/remote analysis workers are internal modes.

- Before participant ID/study start, frames may update the live admin monitor.
- During a study, derived emotion samples can be published through the plugin's
  LSL bridge when selected.
- Raw camera frames are not stored as session video by this architecture.

Emotion values are research signals, not diagnostic measurements.
They are model inferences from captured images, not direct measurements of a
participant's emotion. Detector confidence is an uncalibrated face-detection
score, independent of the model's emotion confidence. A no-face frame is a
valid negative face detection but has no emotion measurement; a worker error
invalidates both channels. Unavailable numeric measurements are `NaN` in XDF.

**Plan the emotion model before the study day.** The analysis model is
separately licensed under non-commercial research terms and ships in no
release, so someone has to provision it once per recording computer — it never
downloads itself. Local analysis also needs Windows x64 or macOS Apple Silicon;
macOS Intel has to send frames to a remote worker. Camera capture, the LSL
bridge and XDF recording are unaffected either way, so a study without the
model still records everything else. The steps are in
`software/study_runner/plugins/sensors/camera_emotion/README.md`.

## Timer And Clock Metadata

Browser warm-up and stimulus timers use monotonic `performance.now()`
deadlines. Visual onset records event ID, monotonic time, estimated server time,
and deadline locally; rendering does not wait for a network response. The
backend also knows the deadline and closes routing/markers idempotently.

The tablet exchanges four timestamps with the server three times at startup
and before a session, then refreshes every 60 seconds and when the tab returns
or reconnects. The lowest-delay exchange supplies one paired offset and RTT;
the selected exchanges are retained with the result. An estimate over 120
seconds old is stale. Events carry the selected exchange ID, age, RTT and
`time_source`; without valid calibration the server receipt is labelled as a
fallback, and a new timed stimulus cannot be prepared. The RTT/2 value is at
best a network-delay bound under a symmetry assumption, not a measurement of
browser rendering, camera exposure or sensor acquisition time. A host wall
clock step is flagged and the affected marker uses LSL receipt time rather
than a guessed historical mapping.

Hidden tabs do not pause a trial. Visibility interruption duration and late
callback delay are stored as quality metadata. Events are buffered locally and
retried with their original event IDs and source times.

Sensors record continuously; a stimulus card never starts or stops them, it
only writes markers. Start and stop go to the actuator plugins the card
selected (a plugin is an actuator when its manifest declares trial `start`
and `stop`):

| Moment | Marker | Actuators |
|---|---|---|
| The stimulus begins | `stimulus_active_start` | start |
| The duration is reached, only when the card does not continue automatically | `stimulus_time_up` | stop, unless they keep running during overtime |
| The card is left (with auto-advance: when the duration is reached) | `stimulus_active_stop` | stop, if not stopped yet |

The server's safety stop fires whichever of these events stops the
actuators: at the planned end, or with overtime at the latest after the
card's maximum overtime. The card's statistics window runs from
`stimulus_active_start` to `stimulus_time_up` (or `stimulus_active_stop`),
so it has the same length for every participant; the overtime from
`stimulus_time_up` to `stimulus_active_stop` is its own window in
`card-summary.json` (`window: "overtime"`) and `overtime_ms` in
`card_events`.

For scientific streams, original source timestamps, LSL time correction, and
XDF clock-offset chunks remain authoritative. Browser/server clock estimates
are event metadata and do not replace those clocks.

## Finalization And Destinations

The participant completion page is shown after the local submission commit,
not after network uploads. A persistent background state machine then freezes
recording, validates sources, merges and validates XDF, builds card summaries,
writes provenance/checksums, and publishes destinations.

Notion reads only `result.json` and `card-summary.json` and upserts by
`session_id`. Nextcloud mirrors the canonical session path, verifies immutable
artifacts by SHA-256, and writes the completion marker last.

Raw plugin XDFs can be purged locally only when:

- merge parity passed;
- the session is fully `completed`, not degraded;
- Nextcloud was enabled; and
- every raw source has a verified matching remote SHA-256.

Backup XDF, merged XDF, JSON, checksums, and manifests remain local. Without
Nextcloud or during an attention/degraded completion, raw sources remain local.
The nine finalization steps and their replay behaviour are in
[plugin-recording-architecture.md](plugin-recording-architecture.md#persistent-finalization).

## When Something Goes Wrong

What each failure costs you, from the study's point of view:

| Situation | Consequence |
|---|---|
| A required plugin is missing | The participant cannot be released; the run does not start. |
| A sensor disconnects mid-run | The participant timer keeps going. The gap is recorded as visible metadata and the admin page warns. |
| The tablet or server connection is lost | Recording continues on a 15-minute worker lease, then closes as `attention_required` if control does not return. |
| The server restarts | Journals are replayed and the worker is reattached. |
| The worker or machine crashes | Readable fragments are preserved and recording resumes in new segments. |
| A required source is missing or corrupt, or the merge does not match | Finalization fails. It never becomes a silent `completed`. |
| The admin acknowledges a documented loss | The session becomes `completed_degraded` and keeps a permanent quality warning. |

[how-recording-quality-works.md](how-recording-quality-works.md) explains what
these states mean and how the numbers behind them are measured.

## Sensor Source Versus Runtime

The lab workspace keeps reference hardware material separate from shipped app
code:

| Area | Purpose |
| --- | --- |
| `../Sensorik/` | Vendor files, experiments, firmware references, and lab notes. |
| `software/study_runner/plugins/` | Trusted, tested runtime plugins discovered and shipped by Study Runner. |

Promote only tested runtime files into a plugin package. Add manifest, schema,
synthetic fixtures, and hardware smoke tests there; do not run experimental
reference code directly from `Sensorik/`.

## Research-Grade Boundary

The architecture supports good scientific practice through independent raw
streams, stable identities, explicit timing, durable provenance, reproducible
derived data, checksums, and visible quality failures. It does not by itself
make Study Runner a medical device or validate it for GCP, 21 CFR Part 11,
HIPAA, clinical diagnosis, or a particular institutional protocol.

Known boundaries include:

- BLE/browser/camera timing is not a physical hardware trigger.
- Sensor and emotion algorithms require device-specific scientific validation.
- A machine crash may lose samples since the most recent durable flush.
- `completed_degraded` data must be interpreted with its quality warnings.
- Full BIDS compliance is not claimed.

## Methods And Limitations Per Sensor

Text for a methods section. State the Study Runner version and the plugin
versions: each session records both in `meta/manifest.json` →
`provenance.software`, and the session view has a button that copies them as a
sentence ("Data were recorded with Study Runner 1.7.0 (plugins: brainbit
1.0.1, mood_meter 1.1.0)."). It names the plugins that produced data (sensors
and cards); upload destinations only copy finished files and are listed in the
session, not in the sentence.

**BrainBit (EEG).** Raw EEG of four channels (O1, O2, T3, T4) at 250 Hz is
recorded in the `eeg` stream; band powers and the attention/relaxation indices
(`bands`, `mental`, 25 Hz) are computed by the manufacturer's NeuroSDK and its
emotion/artifact library. Those indices are proprietary algorithms: report them
as exploratory measures, not as validated constructs, and name the SDK versions
(`pyneurosdk2`, `pyem-st-artifacts`) and the headband firmware, which are
recorded in the `diagnostics` stream (`DEVICE` event). Timestamps are
reconstructed on the host from the SDK's packet callbacks
(`host_callback_reconstructed`), not taken from the headband's clock. Electrode
contact, artifacts, calibration and packet discontinuities are recorded and
must be considered in the analysis.

**AM Hub (radar, bio, valves).** One sample is recorded per frame that the
Parasite AM Hub forwarded (radar LD2450/LD2410B and bio MR60 at about 10 Hz,
valves on change), with the values and units the ESP firmware sent; nothing is
resampled, converted or carried forward. Timestamps are this computer's LSL
time on arrival and include radio (BLE/WiFi), hub and network delay. That
delay is measured per frame and recorded in `latency_ms`: half the radio round
trip the hub measures to each board, plus the time from the hub to this
computer, using the hub's clock (`hub_timestamp`) mapped onto this computer's
clock by a ping every second (the `hub_clock` stream holds every ping, its
round trip and the clock-offset estimate). With the AM Hub setting "Correct
timestamps by the measured latency" (off by default), a frame's timestamp is
its arrival time minus that latency, and `correction_ms` records what was
taken off, so the arrival time can always be rebuilt. `seq` makes losses
countable. The ESP
sends 0 when it has no value (its own 2 s timeout) and repeats its latest
values, so a 0 or a repeated value is not necessarily a new measurement; card
averages leave such zeros out, the XDF keeps them. Heart and breathing rate of
the MR60 are radar estimates, not a clinical measurement. The hub's version is
recorded in `hub_events` (`hello` event).

**MR60 mini radar (heart rate, breathing, distance).** One sample per packet
the MR60 board sends (about 10 Hz over BLE or serial) in `vitals` and
`phases`, with the board's flags, its packet counter (`seq`) and its own
millisecond clock (`device_ms`). Heart and breathing rate are the radar's
estimates, not a clinical measurement; distance is in centimetres. Timestamps
are this computer's LSL time on arrival and include the BLE delay; compare
them with `device_ms` for the board's own timing. Over BLE no quality value is
sent (NaN).

**All sensors.** The 1 Hz backup file is derived (latest value per second) and is for
quality control and as a fallback, not for analysis.

### Hardware Acceptance Checklist

Before data of a setup are used for analysis, record and keep once per setup
(computer, hub/headband, firmware):

1. **Latency:** a known physical event (for AM Hub a person stepping into the
   field at a marked moment, for BrainBit an LSL marker together with a
   blink or tap artifact) against its XDF timestamp; report mean and spread.
   For AM Hub also report the recorded `latency_ms` and whether timestamps
   were corrected (`correction_ms` non-zero), and check that `hub_clock`
   shows a valid offset with a small uncertainty.
2. **Losses:** a 60-minute recording; count `seq` gaps (AM Hub), packet
   discontinuities (BrainBit) and reconnects.
3. **Empty room / no headband contact:** the values the device reports when
   there is nothing to measure (for AM Hub the ESP zeros).
4. **Reference comparison:** AM Hub heart and breathing rate against a chest
   strap or a pulse oximeter; BrainBit eyes-open/eyes-closed alpha.
5. **Interruption:** unplug or switch off a board / the headband during a
   recording and check that the gap is visible and the recording continues.

## Terms

- **LSL**: Lab Streaming Layer, the common live stream and clock layer.
- **XDF**: the canonical multi-stream recording container.
- **Native XDF**: a plugin's source-rate, non-resampled raw recording.
- **Derived backup**: the reduced slowest-grid recovery/QC recording.
- **Plugin catalog**: the validated API-v5 manifest description returned to core/UI.
- **Recording worker**: the detached Python process that owns LSL inlets and
  recording orchestration.
- **XDF core**: the small C-compatible library wrapping the official XDFWriter.
