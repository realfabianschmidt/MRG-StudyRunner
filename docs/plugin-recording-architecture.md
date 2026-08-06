# Plugin Recording Architecture

This document is the implementation contract for plugin discovery, LSL
acquisition, canonical XDF recording, session finalization, recovery, and data
publication. It describes the source-server workflow. Release bundles and Apple
signing/notarization are separate work.

## Design Boundary

Study Runner deliberately uses two implementation layers:

- Python and browser JavaScript own plugins, UI, LSL inlet orchestration,
  leases, journals, finalization, statistics, and uploads.
- A small C-compatible native library owns only the audited XDFWriter boundary.

The native layer does not contain HTTP, LSL discovery, plugin logic, session
policy, statistics, or upload code. This keeps the platform-specific surface
small while using the official LabRecorder XDFWriter implementation for the
container format. XML is used inside XDF stream headers and metadata; replacing
XDF with stand-alone XML would lose the timed multi-stream container and is not
an equivalent simplification.

Canonical recording is supported on Windows x64, macOS Intel, and macOS Apple
Silicon. The regular Python server can still be developed and tested on Linux,
but recording readiness must fail closed there.

The camera/emotion analysis runtime has a narrower local support matrix:
`local_worker` is supported on Windows x64 and macOS Apple Silicon. Because
TensorFlow/tf-keras has no compatible CPython 3.12 macOS Intel wheel, Intel Macs
use `remote_worker`; their browser capture, host LSL bridge, server, and XDF
recording remain supported.

## Source Setup

Use the platform installer to create `.venv`, install the applicable pinned
Python dependencies, and build and verify the native core:

```powershell
.\tools\install-windows.ps1 -InstallSystemDependencies
```

```bash
bash tools/install-macos.sh --install-system-dependencies
```

The installer supplies system packages only when that explicit option is used,
then builds only the current platform, runs CTest, and runs a synthetic writer
smoke test. `python tools/setup_recording_worker.py` remains the core-only
developer command and never installs a compiler, package manager, or Python
package automatically.

Dependency resolution is shared between source installers, CI, and the
camera/emotion repair action:

- `software/constraints/py312-bootstrap.txt` pins pip;
- `software/constraints/py312-common.txt` pins the release-tested common set,
  including `numpy 1.26.4`, `pylsl 1.18.2`, and `pyxdf 1.16.8`;
- `software/constraints/py312-local-emotion.txt` pins the high-risk local
  inference stack and is not selected on macOS Intel.

These are bounded compatibility constraints, not a complete hash-locked
transitive wheel manifest. Clean Windows and macOS release jobs therefore
remain mandatory before publication.

- Windows requires the Visual Studio C++ Build Tools workload.
- macOS requires the Xcode Command Line Tools.

Generated files live below
`software/.build/xdf_core/<platform-arch>/` and are not committed. The core
locator checks `STUDY_RUNNER_XDF_CORE` first, then that exact local build path.
`worker-build.json` records platform, architecture, ABI, pinned writer revision,
source-lock fingerprint, binary SHA-256, and supported features. A missing,
wrong-platform, stale, or ABI-incompatible core blocks only studies that select
a recording source. The readiness response includes the setup command.

After setup, normal operation is:

```powershell
.\tools\start-windows.ps1
```

```bash
bash tools/start-macos.sh
```

Both launchers use the repository `.venv` directly. `cd software && python
server.py` remains the equivalent developer command inside an activated
environment.

## Trusted Plugin Layout

Only code shipped in `software/study_runner/integrations/` is discovered. There
is no browser upload, dependency installer, marketplace, or third-party plugin
execution path.

```text
integrations/<plugin-key>/
  manifest.json
  plugin.py
  adapter.py              # optional
  worker/                 # optional plugin-internal process
  assets/                 # optional
```

`manifest.json` is validated before `plugin.py` is imported. Invalid plugins,
duplicate plugin keys, duplicate stream source IDs, illegal transport pairs,
and missing declared handlers are isolated as catalog entries with
`status: invalid`. They do not crash server startup. A study that requires an
invalid or missing plugin cannot pass readiness.

Every non-hidden top-level directory is a discovery candidate. A missing
manifest is therefore visible as `invalid`, not silently ignored. Intentional
internal implementation packages and one-release compatibility shims carry an
explicit `.pluginignore` marker.

The public catalog is `GET /api/plugins/catalog`. Generic UI surfaces consume
that response and must not maintain sensor-key lists.

## Manifest API v3

Every manifest provides:

- `api_version: 3`
- stable `plugin_key`, semantic `version`, `category`, and `entry_point`
- `ui` metadata, including label, description, order, and visibility
- `settings.machine`, `settings.study`, and `settings.card_actions` schemas
- declarative `capabilities`
- stable stream metadata for every LSL stream
- poll, timeout, clock, data-rate, and backpressure metadata

The UI visibility contract is:

```json
{
  "ui": {
    "visibility": {
      "dashboard": true,
      "settings_hub": true,
      "study_settings": true,
      "destination_settings": true
    }
  }
}
```

Omitted flags default to `true` for old v3 manifests. Notion and Nextcloud use
`dashboard: false`, `settings_hub: false`, `study_settings: true`, and
`destination_settings: true`. Their job progress is still part of finalization.
Internal marker and clock providers are hidden. XDF is infrastructure and is
never represented as a selectable plugin or settings menu.

Supported capabilities include:

- `study_sensor`
- `acquisition_transport`
- `lsl_stream_provider`
- `recording_source`
- `backup_projection`
- `readiness`
- `runtime_control`
- `health`
- `machine_settings`
- `study_settings`
- `card_actions`
- `admin_actions`
- `participant_actions`
- `participant_ingest`
- `upload_destination`

Plugins whose runtime modes differ by operating system declare that support in
the optional `readiness` capability instead of relying on plugin-key checks in
the server or UI. `mode_setting` names the machine-setting field,
`default_mode` defines its fallback, and `platform_modes` contains a mandatory
`default` list plus exact overrides such as `macos-x64`. The generic readiness
gate blocks an unsupported required mode and returns the supported alternatives
to the admin UI.

```json
"readiness": {
  "mode_setting": "worker_mode",
  "default_mode": "local_worker",
  "platform_modes": {
    "default": ["local_worker", "remote_worker"],
    "macos-x64": ["remote_worker"]
  }
}
```

A manifest may declare only capabilities for which its exported plugin object
provides the required handler. `admin_actions` are invoked through the generic
plugin action route and are allow-listed by manifest action key. Action request
bodies are closed by `payload_schema`; dynamic instances can be projected from
cached plugin status through manifest-declared paths. Unknown fields and wrong
types are rejected before plugin code runs. Participant actions and ingest use
closed `actions`/`inputs` key lists and the generic participant route. Browser
ingest additionally requires `sequence_number` and one of the manifest-declared
source timestamp fields before plugin code runs.

An upload destination implements `publish_destination(context, payload)` and
declares its stable queue `destination` plus finalization policy in the
manifest: `requires_valid_result`, `publish_on_attention`,
`republish_on_degraded`, and `purge_verified_sources`. At most one installed
destination may grant source purge. Legacy flat-field aliases live below
`upload_destination.legacy`; they are read during migration and never emitted
by a newly saved study. Destination settings and finalization steps therefore
need no core plugin-key branch.

Plugins which must rebuild disabled adapter state declare
`lifecycle.reinitialize_on_disable: true`; the registry has no lifecycle key
set.

Optional trusted UI extensions are declared below `ui.extensions` (`dashboard`
and `participant`). Entry modules and any relative JavaScript imports listed in
`ui.assets` are checked for relative-path containment and existence during
discovery. The server serves only this allow-list, a broken optional module is
failure-isolated, and core UI uses its generic fallback. Timeline aliases and
preferred numeric channels live in `ui.timeline`, never in a core sensor map.

Every `study_sensor` that records declares
`recording_source.primary_stream`. Readiness waits for a fresh sample from this
manifest-named stream; it does not guess that the first listed stream is the
scientifically relevant one.

Study selections use one stable schema:

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

Selected sensors default to `required: true`. Cards use
`plugin_actions[plugin_key]`. Legacy settings are normalized while loading;
newly persisted studies contain only the v3 destination form.

## Acquisition Transport Matrix

LSL is the common acquisition boundary whenever data crosses a process or
network boundary, but it is not the BLE wire protocol.

| Transport | Manifest delivery | Rule |
| --- | --- | --- |
| LAN | `native_lsl` | The source publishes a stable LSL stream directly. |
| WLAN | `native_lsl` | The source publishes a stable LSL stream directly. |
| BLE | `host_lsl_bridge` | A local adapter receives BLE data and republishes it through LSL. |
| Serial | `host_lsl_bridge` | A local adapter republishes device samples through LSL. |
| Browser HTTPS | `host_lsl_bridge` | The server validates and republishes browser samples through LSL. |
| Local hardware | `host_lsl_bridge` | The local device adapter publishes the LSL stream. |
| Network adapter | `host_lsl_bridge` | The adapter normalizes the source and publishes LSL locally. |
| Internal provider | `host_lsl_bridge` | Hidden markers and clock diagnostics are recorded once. |

Browser sources require HTTPS, a heartbeat, monotonic sequence numbers, and a
source timestamp. BLE does not carry LSL itself; LSL starts after the host
adapter has decoded the BLE packets. This preserves transport diagnostics while
giving the recorder one consistent inlet model.

Any plugin with `recording_source` must also declare an LSL provider. Its
canonical recording and LSL bridge cannot be disabled through generic machine
or study settings.

## Camera And Emotion

Camera capture and emotion analysis have one public catalog key:
`camera_emotion`. Browser capture, local analysis, and remote analysis are
internal operating modes of that plugin. Compatibility shims may read old
`emotion_worker` settings for one migration period, but no second catalog entry
is exposed. Raw video frames are not an XDF stream and are not retained unless
a future study protocol explicitly introduces and documents that behavior.

## Recording Worker

Flask starts a detached Python worker and communicates over loopback. Each
session uses a random token, monotonically increasing generation fencing, and
idempotent `command_id` values. The worker persists its endpoint, lease, active
generation, and operation ledger in the session directory so Flask can reattach
after a restart.

Supported commands are:

- `health`
- `start_recording_source`
- `start_backup_projection`
- `refresh_lease`
- `freeze_session`
- `merge_xdf`
- `shutdown_session`

The worker owns LSL inlets and segment allocation. Each inlet is consumed on a
bounded thread and samples cross the native ABI in validated batches. The
worker records the actually loaded liblsl version in runtime provenance.

Before the participant screen is released, every required source must be
connected, its first segment must have been created exclusively, and at least
one fresh primary sample must have arrived. Missing optional sources produce a
warning. The same fail-closed preflight probes the pinned Python packages,
`pylsl`, and the actually loadable native `liblsl`, so a broken LSL runtime is
reported before the study starts rather than in the detached child process.

## Native XDF Core

The stable C ABI exposes only:

- ABI, writer-version, and feature probes
- exclusive XDF creation
- stream headers and footers
- typed numeric and string batches
- clock-offset chunks
- boundary and durable flush
- controlled close or abort
- checked append of unchanged source chunks during merge

C++ objects, ownership, exceptions, and standard-library types never cross the
ABI boundary. Every function returns an error code; error text is copied into a
caller-provided buffer. Buffer sizes, channel counts, sample types, and state
transitions are checked before a writer call.

The core writes a boundary around every ten seconds and performs a durable
flush at least every five seconds. A crash never resumes an existing segment.
Recovery starts the next monotonically numbered file.

## Raw And Backup Recording

Each active recording plugin receives independent segments:

```text
raw/plugins/<plugin-key>/part-0001.xdf
raw/plugins/<plugin-key>/part-0002.xdf
```

Native rates and raw timestamps are retained. The backup grid is calculated
once at session start as the smallest positive `backup_projection.rate_hz` of
all active sensor plugins:

```text
raw/backup/slowest-grid_<rate>hz.xdf
```

The worker samples its last-received cache on each grid deadline. It never
silently carries a missing or stale value forward. Numeric value channels use
`NaN`, accompanied by `valid`, `sample_age_ms`, sequence, and status channels.
Status is one of `missing`, `valid`, `stale`, or `degraded`. Metadata records
`derived_backup`, active plugins, source rates, staleness rules, and projection
strategy. This file is a recovery/QC artifact, not an equivalent raw-data copy.

## Merge And Validation

Merge is two-pass and bounded-memory. Pass one indexes streams and chunks; pass
two assigns deterministic conflict-free container stream IDs and copies sample
and clock payloads unchanged. Only container IDs and safe provenance metadata
may change. There is no resampling, clock synchronization, or dejittering.

`derived/session.xdf` is validated with pinned PyXDF in raw mode. For every
source stream, validation compares:

- stream metadata
- sample count
- first and last raw timestamps
- complete raw timestamp sequence
- clock-offset chunks
- normalized sample-data hash

Any mismatch is a finalization failure, never a warning-only success.

## Canonical Session Layout

```text
saved_results/
  <study>/
    participants/
      <participant>/
        sessions/
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

The original participant identifier remains in JSON. Path components are
bounded and sanitized. UTC start plus immutable session ID prevents collisions.
Legacy flat result folders are left untouched and are not part of the canonical
session browser.

## Timer And Event Journal

Warm-up and stimulus timing use `performance.now()` deadlines; UI intervals
only repaint the remaining time. Visual onset records an event ID, monotonic
time, estimated server time, and deadline before any network round trip. The
backend also knows the intended deadline and closes routing/markers
idempotently.

Page visibility does not pause a trial. Visibility interruption duration and
late-callback delay are quality metadata. Marker and card events are buffered
locally and retried with their original source time and event ID.

## Persistent Finalization

`POST /api/results` performs an atomic local submission commit. Only after that
commit succeeds does it return HTTP 202 and allow the participant completion
screen. The remaining work is journaled and invisible to the participant.

1. `commit_submission`
2. `freeze_recording`
3. `validate_sources`
4. `merge_xdf`
5. `validate_merge`
6. `build_card_summary`
7. `write_result_manifest`
8. one independent `publish_<plugin-key>` step per manifest-declared destination
9. `purge_local_sources`

The same `submission_id` resolves to the same job. Every transition and retry
is persisted. Destination definitions and policies are embedded in the job so
replay remains deterministic across plugin upgrades.

Card summaries are derived only from the validated merged XDF and half-open
marker windows `[start, end)`. Numeric channels provide `count`, `valid_count`,
`mean`, `min`, `max`, and sample `stddev`; `stddev` is null below two valid
samples. The summary also records expected samples, coverage, missing/drop
counts, maximum gap, time source, and plugin status. Boolean channels use 0/1;
categorical channels provide counts and a mode.

Recovery segments with the same manifest plugin key and stable LSL source ID
are joined logically before a Card statistic is computed. Their structural
stream metadata must match; otherwise finalization requires attention. This
prevents `part-0001` and `part-0002` from producing two misleading partial
means or two independent full-window coverage values.

Source validation also requires a readable stream footer whose declared sample
count matches the raw samples PyXDF can read. A truncated final chunk remains
on disk as recovery evidence but can never pass silently as a complete source.

Nextcloud mirrors only the canonical session path, verifies immutable artifacts
by SHA-256, and uploads the completion marker last. Raw plugin XDFs may be
purged locally only after valid merge parity, `completed` state, enabled
Nextcloud publication, and verified remote hashes for every source. Backup,
merge, JSON, checksums, and manifests remain local.

## Recovery And Quality States

- A sensor disconnect triggers reconnect attempts, visible gaps/drop metadata,
  and an admin warning without stopping the participant timer.
- A missing tablet heartbeat starts a 15-minute lease. Recording continues and
  then closes with `attention_required` if no controller returns.
- A Flask crash is handled by journal replay and worker reattachment.
- A worker crash preserves all segments and starts a new segment generation.
- A machine crash can lose at most data not covered by the last durable flush;
  readable fragments remain recovery artifacts.
- Missing required XDFs, corrupt chunks, merge mismatch, or summary failure can
  never become silent `completed`.
- Available sources may still be backed up to Nextcloud with
  `ATTENTION_REQUIRED.json`; Notion waits for valid or explicitly degraded data.

Job states are `queued`, `running`, `attention_required`, `completed`, and
`completed_degraded`. Step states are `pending`, `running`, `retrying`, `done`,
`failed`, and `skipped`. An admin may retry a step or acknowledge a documented
loss with a reason. The latter produces `completed_degraded` and keeps the
quality warning in published summaries.

## Verification Gates

Automated acceptance includes manifest isolation, a no-core-change fixture
plugin, synthetic 250/10/1 Hz acquisition, all XDF primitive types, string
markers, clock offsets, stale backup values, reconnects, segment changes,
boundary/flush behavior, truncated final chunks, merge parity, fake browser
clocks, worker/server kill tests, finalization replay, duplicate submission,
destination idempotency, and guarded purge.

Native builds must pass CTest plus Python LSL/XDF smoke tests on Windows x64,
macOS Intel, and macOS ARM. Linux runs only Python, JavaScript, schema, and
static tests. Physical BrainBit, MR60, tablet, camera/emotion, Notion, and
Nextcloud smoke tests remain a required release gate before recording becomes
the default in production.

## What the session viewer reads from your stream

The completed-session timeline draws itself from the merged XDF, not from any
per-sensor code or configuration. A plugin that publishes a well-described LSL
stream therefore appears in the viewer with correct labels and the right kind of
track without a single change in the core.

Put these in the stream header when you create the outlet:

| Field | Used for |
|---|---|
| `name` | the track group's heading, shown to the operator |
| `nominal_srate` | rendering: at or above 20 Hz a channel is drawn as a filled waveform, below it as a line |
| `desc/channels/channel/label` | the per-track label |
| `desc/channels/channel/type` | rendering override: a continuous LSL type (`EEG`, `ECG`, `EMG`, `EOG`, `GSR`, `EDA`, `PPG`, `Respiration`, `Accelerometer`, …) is drawn as a waveform whatever the rate says; `Markers` is drawn as event ticks |
| `desc/channels/channel/unit` | shown next to the track label |
| `desc/study_runner/plugin_key` | ties the stream back to your plugin |

Nothing is mandatory. Without a rate or a type a channel is drawn as a line;
without labels the viewer falls back to the numeric keys in the samples and
hides obvious bookkeeping fields (sequence numbers, timestamps, jitter, drop
counters). The result is still readable — it just does not say `microvolts`.

**Optional curation.** A sensor that records twenty numeric fields would arrive
as twenty tracks. Listing the ones worth looking at in your manifest limits the
viewer to exactly those, in your order:

```json
"ui": { "timeline": {
  "lane_aliases": ["mr60"],
  "preferred_channels": ["heartPhase", "heartRate", "breathPhase", "breathRate"]
}}
```

Declaring nothing is equally supported and is the path a new plugin should be
able to take: everything meaningful in the stream is then discovered from the
header. Note that `preferred_channels` filters, so a channel you leave out will
not be drawn even though it was recorded — the recording itself is unaffected.

To see any of this without hardware, `tools/make_timeline_fixture.py` writes a
synthetic session with three streams at 250 Hz, 64 Hz and 1 Hz.
