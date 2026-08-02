# Developer Guide

Study Runner uses trusted built-in integration plugins. A plugin is a Python
package below `software/study_runner/integrations/`; there is no web upload,
marketplace, automatic dependency installation, or untrusted code path.

The complete recording contract is in
`plugin-recording-architecture.md`. This guide focuses on the code layout and
the smallest safe plugin workflow.

## Naming And Structure

- `software/` is the application root and stays lowercase.
- Python packages, modules, services, and plugin keys use `snake_case`.
- Browser files and docs use descriptive `kebab-case`.
- Active technical docs and code comments are English.
- Keep HTTP handlers thin; validation and policy belong in focused services.
- Keep hardware-specific code inside its integration package.

## Important Files

- `software/study_runner/integrations/plugin_api.py`: shared context and plugin
  callback type.
- `software/study_runner/integrations/plugin_catalog.py`: trusted directory
  discovery, manifest-v3 validation, duplicate isolation, and public catalog.
- `software/study_runner/integrations/registry.py`: compatibility facade over
  the discovered, validated plugins.
- `software/study_runner/backend/services/sensor_coordinator_service.py`:
  lifecycle and status orchestration.
- `software/study_runner/backend/services/recording_runtime.py`: Flask-side
  worker orchestration; it contains no XDF encoding.
- `software/study_runner/backend/recording/`: worker protocol, session paths,
  segment allocation, recovery, and XDF validation contracts.
- `software/study_runner/recording_worker/`: detached Python worker.
- `software/recording_worker/native/`: native XDF-core source and CTest.
- `software/study_runner/backend/services/finalization_service.py`: persistent
  finalization transitions only.
- `software/study_runner/backend/services/card_summary_service.py`: pure merged
  XDF-to-JSON derivation.
- `software/study_runner/backend/services/artifact_manifest_service.py`:
  checksums, provenance, markers, and guarded purge.

## Required Plugin Shape

```text
software/study_runner/integrations/my_new_sensor/
  __init__.py
  manifest.json
  plugin.py
  adapter.py          # optional
  worker/             # optional internal process
  tools/              # optional diagnostics
  firmware/           # optional device firmware
```

The server discovers the folder automatically. Do not add a central import
entry. Discovery validates `manifest.json` before it imports `plugin.py`.

`plugin.py` exports one object:

```python
from study_runner.integrations.plugin_api import IntegrationPlugin

PLUGIN = IntegrationPlugin(
    key="my_new_sensor",
    label="My new sensor",
    category="biosignal",
    config_key="my_new_sensor",
    initialize=initialize,
    get_status=get_status,
)
```

The exported key must match `manifest.json`. Add defaults to
`software/study_content/settings/hardware_settings.json` only for genuine
machine state; per-study choices belong in the manifest's study schema.

## Manifest API v3

Every manifest includes identity, version, category, entry point, UI metadata,
settings schemas, capabilities, polling/timeout policy, clock domain, and
backpressure policy. A stream provider also declares stable source IDs,
channels, types, units, nominal rates, and timestamp origin.

Use exactly these UI visibility flags:

```json
{
  "dashboard": true,
  "settings_hub": true,
  "study_settings": true,
  "destination_settings": true
}
```

Missing flags default to `true` for v3 compatibility. The catalog response is
the source for generic UI; do not add key-specific sensor lists, labels,
settings fields, status cards, or action buttons to core JavaScript.

Rich presentation is optional plugin code: declare `ui.extensions.dashboard`
or `ui.extensions.participant` as a relative `.js` entry point and list its
relative imports in `ui.assets`. Discovery and the asset route enforce the
manifest allow-list and directory containment. Keep a complete generic
fallback. Completed-session channel order belongs in
`ui.timeline.preferred_channels` (with optional `lane_aliases`). Admin-action
payloads belong in the manifest's closed `payload_schema`, including dynamic
status-instance mappings where needed.

Important capabilities are:

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

Capabilities are promises. If a manifest declares a handler-backed capability,
the plugin object must implement the corresponding callback or discovery marks
the plugin invalid.

All non-hidden top-level integration directories are candidates. A missing
`manifest.json` is reported as invalid. Only an intentional helper or temporary
compatibility package may opt out with a documented `.pluginignore` file.
Recording study sensors must name an existing
`recording_source.primary_stream`; readiness waits for that stream's first
fresh sample.

## Acquisition Rules

- LAN and WLAN sources use `native_lsl`.
- BLE, serial, browser HTTPS, local hardware, and adapter-based sources use
  `host_lsl_bridge`.
- Browser sources require HTTPS, heartbeat, sequence, and source timestamps.
- BLE packets do not carry LSL; the local adapter decodes them and publishes
  the resulting LSL stream.
- A `recording_source` also declares `lsl_stream_provider` and may not expose a
  setting that disables canonical recording or its LSL path.

Use stable source IDs. Changing one creates a scientifically different stream
identity and requires a migration note plus fixture updates.

## Callback Responsibilities

A plugin implements only the handlers required by its capabilities:

- `initialize(context)`: create local clients, bridges, or device state.
- `get_status(context)`: return a fast cached status.
- `start`, `stop`, `restart`: runtime control.
- `readiness(context)`: detailed preflight checks.
- `run_admin_action(context, action_key, payload)`: manifest-allow-listed repair or
  diagnostic actions.
- `run_participant_action(context, action_key, payload)` and
  `ingest_participant(context, input_key, payload)`: closed, manifest-declared
  participant operations; browser payloads retain original source time and
  sequence.
- `publish_destination(context, payload)`: execute one persistent upload job;
  finalization behavior comes from the manifest destination policy rather than
  a core destination list.
- `on_trial_start`, `on_trial_stop`, `on_trial_marker`: idempotent trial events.
- `get_interval_summary`: legacy compatibility only; canonical card summaries
  come from merged XDF.
- `export_interval_samples`: legacy sidecar compatibility only; it is not the
  canonical raw-data path.

Health polling is manifest-paced and must return cached state within the
declared timeout. A slow hardware request must run behind the plugin boundary,
not block the aggregate dashboard status request.

## Adding A Recording Sensor

1. Add the package, manifest, adapter, and tests.
2. Choose the transport/delivery pair from the transport matrix.
3. Publish stable LSL streams with explicit channels, units, format, rate, and
   clock domain.
4. Declare `recording_source` and at least one numerical
   `backup_projection` when a central QC projection is meaningful.
5. Implement readiness so a required sensor proves connection and a fresh
   primary sample before participant release.
6. Add manifest-driven machine/study/card settings; never add a sensor-key
   branch to generic UI.
7. Add synthetic LSL/XDF fixtures covering native rate, gaps, reconnect, stale
   projection values, merge parity, and card statistics.
8. Perform a hardware smoke test before enabling the plugin by default.

## Camera, Destinations, And Infrastructure

`camera_emotion` is one public plugin. Camera capture and local/remote emotion
workers are internal modes. Notion and Nextcloud are destination plugins and
may hide from dashboard/settings-hub device lists while remaining visible in
study destination settings and finalization. XDF, markers, and clock diagnostics
are recording infrastructure and do not get user-facing plugin menus.

## Source Recording Setup

Run once per recording computer:

```bash
python tools/setup_recording_worker.py
```

The command checks CMake and the native compiler, builds only the current
platform, runs CTest, and performs a synthetic writer smoke test. It installs
nothing automatically. Generated output belongs below `software/.build/` and
must not be committed.

## Required Checks

```bash
python -m unittest discover -s software/tests
node --test software/tests/js/*.test.mjs
python tools/setup_recording_worker.py --probe-only --require-canonical
git diff --check
```

The native setup/smoke test is required only on supported recording platforms.
Linux must pass Python, JavaScript, schema, and static tests and must report
recording as unsupported.

## General Rules

- Preserve source timestamps and LSL clock diagnostics; do not replace them
  with coordinator response time.
- Never invent missing samples or forward-fill stale backup values.
- Never continue writing a segment after a worker crash.
- Never mark a merge complete without parity validation.
- Never purge raw data before verified remote SHA-256 parity.
- Keep secrets out of browser responses and committed settings.
- Store local secrets only in the ignored `local_secrets.json` path.
- Keep English and German locale key sets identical.
