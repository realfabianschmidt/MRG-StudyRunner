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
software/saved_results/
  <study>/participants/<participant>/sessions/
    <YYYYMMDDTHHMMSSZ>__<session-id>/
      submission.json
      result.json
      card-summary.json
      manifest.json
      checksums.sha256
      finalization-state.json
      logs/finalization.jsonl
      raw/plugins/<plugin>/part-0001.xdf
      raw/backup/slowest-grid_<rate>hz.xdf
      derived/session.xdf
      COMPLETE.json | ATTENTION_REQUIRED.json
```

The original pseudonymous participant ID is preserved in JSON. Sanitized path
components, UTC start, and immutable session ID prevent collisions when one
participant repeats a study. Pre-1.0 flat result folders are an archival compatibility surface: they stay
readable but are not moved and are not part of the canonical completed-session
browser.

`submission.json` is the atomic local participant commit. `result.json` is the
published result view. `manifest.json` and `checksums.sha256` record provenance
and artifact integrity. `finalization-state.json` plus the JSONL log make every
step and retry replayable after a process restart.

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

Hidden tabs do not pause a trial. Visibility interruption duration and late
callback delay are stored as quality metadata. Events are buffered locally and
retried with their original event IDs and source times.

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

## Terms

- **LSL**: Lab Streaming Layer, the common live stream and clock layer.
- **XDF**: the canonical multi-stream recording container.
- **Native XDF**: a plugin's source-rate, non-resampled raw recording.
- **Derived backup**: the reduced slowest-grid recovery/QC recording.
- **Plugin catalog**: the validated API-v5 manifest description returned to core/UI.
- **Recording worker**: the detached Python process that owns LSL inlets and
  recording orchestration.
- **XDF core**: the small C-compatible library wrapping the official XDFWriter.
