# Sensors And Data

## Active Study

The active study is stored in
`software/study_content/settings/study_config.json`. Saved presets live in
`software/study_content/studies/` as `.study-runner` files.

Plugin choices use the manifest-v3 schema:

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
participant repeats a study. Old flat result folders are not moved and are not
part of the canonical completed-session browser.

`submission.json` is the atomic local participant commit. `result.json` is the
published result view. `manifest.json` and `checksums.sha256` record provenance
and artifact integrity. `finalization-state.json` plus the JSONL log make every
step and retry replayable after a process restart.

## LSL Acquisition

LSL is the common acquisition boundary for recorded sensor and marker streams:

- Network-native LAN/WLAN sources publish LSL directly.
- BLE, serial, local hardware, browser, and adapter sources are republished by a
  host-side LSL bridge.
- BLE itself is not an LSL transport.
- Browser samples require HTTPS, heartbeat, sequence number, and source time.

Each plugin manifest declares stable stream/source IDs, channel names, units,
format, nominal rate, and clock domain. Marker and clock-diagnostic streams are
hidden recording providers and appear exactly once in the merged session.

## Native Raw XDF

Each active recording plugin receives its own append-never XDF segments. The
detached Python worker owns LSL inlets and sends validated batches to the small
native XDF core. Flask never encodes XDF bytes.

A worker restart creates `part-0002.xdf`; it never appends to a potentially
damaged `part-0001.xdf`. Boundaries and durable flushes limit crash loss. Source
headers, raw timestamps, native rates, samples, and clock offsets are retained.

The final `derived/session.xdf` combines all source segments without resampling,
clock synchronization, or dejittering. Validation compares metadata, sample
counts, raw timestamps, clock offsets, and normalized data hashes against every
source. A parity failure cannot become a normal completed session.

## Slowest-Grid Backup

At session start, the worker selects the smallest positive backup rate declared
by the active sensor plugins. It samples its last-received cache at that shared
deadline and writes a separate `derived_backup` XDF.

Missing and stale values are not forward-filled. Value channels receive `NaN`;
companion channels report validity, sample age, sequence, and a status of
`missing`, `valid`, `stale`, or `degraded`. The backup is useful for recovery and
quality control, but its reduced rate means it is not equivalent to native raw
data.

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

## Failure Meaning

- A missing required plugin blocks participant release.
- A runtime disconnect creates visible reconnect/gap/drop metadata and an admin
  warning but does not stop the participant timer.
- Lost tablet or Flask control starts a 15-minute worker lease. Recording
  continues, then closes with `attention_required` if control does not return.
- Flask restart replays journals and reattaches to the worker.
- Worker/machine crashes preserve readable fragments and use new segments.
- Missing/corrupt required sources, merge mismatch, or summary failure never
  becomes silent `completed`.
- The admin may retry or acknowledge documented loss. Acknowledgement creates
  `completed_degraded` with a persistent quality warning.

## Sensor Source Versus Runtime

The lab workspace keeps reference hardware material separate from shipped app
code:

| Area | Purpose |
| --- | --- |
| `../Sensorik/` | Vendor files, experiments, firmware references, and lab notes. |
| `software/study_runner/integrations/` | Trusted, tested runtime plugins discovered and shipped by Study Runner. |

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
- **Plugin catalog**: the validated API-v3 description returned to core/UI.
- **Recording worker**: the detached Python process that owns LSL inlets and
  recording orchestration.
- **XDF core**: the small C-compatible library wrapping the official XDFWriter.
