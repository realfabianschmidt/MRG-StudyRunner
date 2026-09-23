# Developer Guide

Study Runner uses trusted built-in integration plugins. A plugin is a Python
package below `software/study_runner/plugins/`; there is no web upload,
marketplace, automatic dependency installation, or untrusted code path.

The complete recording contract is in
`plugin-recording-architecture.md`. This guide focuses on the code layout and
the smallest safe plugin workflow.

## Naming And Structure

- `software/` is the application root and stays lowercase.
- Python packages, modules, services, and plugin keys use `snake_case`.
- Browser files and docs use descriptive `kebab-case`, not numbered
  prefixes. Files in `docs/archive/` keep the names they were written
  under, numbering included, because they are a dated record.
- Active technical docs and code comments are English.
- Keep HTTP handlers thin; validation and policy belong in focused services.
- Keep hardware-specific code inside its integration package.

## Important Files

- `software/study_runner/contracts/plugin_api.py`: shared context and
  plugin callback type.
- `software/study_runner/plugin_framework/plugin_catalog.py`: trusted
  directory discovery, manifest validation (api_version 5), duplicate
  isolation, and public catalog.
- `software/study_runner/plugin_framework/registry.py`: lookup and generic
  dispatch facade over the discovered, validated plugins.
- `software/study_runner/plugin_framework/process_host.py`: supervises every
  plugin's `driver.py` subprocess (start/stop/restart, line-oriented
  console, reserved-prefix RPC).
- `software/study_runner/plugin_framework/driver_runtime.py`: runs inside
  that subprocess; imports the plugin's own `plugin.py` and dispatches to it.
- `software/study_runner/data_core/host/sensor_coordinator_service.py`:
  lifecycle and status orchestration.
- `software/study_runner/data_core/host/recording_runtime.py`:
  Flask-side worker orchestration; it contains no XDF encoding.
- `software/study_runner/data_core/host/`: worker protocol, session paths,
  segment allocation, recovery, and XDF validation contracts.
- `software/study_runner/data_core/worker/`: detached Python worker.
- `software/recording_worker/native/`: native XDF-core source and CTest.
- `software/study_runner/runtime_core/delivery/finalization_service.py`:
  persistent finalization transitions only.
- `software/study_runner/runtime_core/studies/card_summary_service.py`:
  pure merged XDF-to-JSON derivation.
- `software/study_runner/runtime_core/delivery/artifact_manifest_service.py`:
  checksums, provenance, markers, and guarded purge.

See `file-guide.md` for the complete, one-line-per-file map; the list above
is only the files worth knowing before touching plugin code.

## Required Plugin Shape

```text
software/study_runner/plugins/sensors/my_new_sensor/
  __init__.py
  manifest.json
  driver.py           # the only process entry point; a one-line wrapper
  plugin.py            # runs inside the driver.py subprocess, not the host
  adapter.py          # optional
  worker/             # optional internal process
  tools/              # optional diagnostics
  firmware/           # optional device firmware
  README.md           # what it does, its architecture, and where any
                       # vendored/SDK-derived code in it comes from
```

The server discovers the folder automatically. Do not add a central import
entry. Discovery validates `manifest.json`, but never imports `plugin.py`
into the host process — only `driver.py`, as a supervised subprocess, does
that (see "Manifest API v5" below). `driver.py` is a one-line wrapper:

```python
from study_runner.plugin_framework.driver_runtime import run_plugin_driver

if __name__ == "__main__":
    raise SystemExit(run_plugin_driver("my_new_sensor"))
```

`plugin.py` exports one object:

```python
from study_runner.plugin_framework.plugin_api import Plugin

PLUGIN = Plugin(
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

## Plugin SDK

`tools/plugin_sdk.py` writes the folder shape above for you and checks
it, instead of copying an existing extension by hand:

```bash
python tools/plugin_sdk.py new sensors my_new_sensor   # or: cards, destinations, outputs
python tools/plugin_sdk.py validate my_new_sensor      # is the manifest valid?
python tools/plugin_sdk.py check-runtime my_new_sensor # does it actually start?
```

`validate` and `check-runtime` do not have their own copy of the validation
rules — they call the exact same code the real server uses, so a "yes" here
means the extension really works, not just that it looks right. There is
also a generated reference file at
`tools/plugin_templates/manifest.schema.json` describing a manifest's
outer shape (regenerate it with `schema --write` after changing
`contracts/manifest.py`); it does not cover each capability's own fields,
since `validate` already checks those for real.

For a sensor, `tools/synthetic_lsl_source.py <plugin_dir>` reads the stream
your manifest already declares and pushes fake-but-plausible samples on LSL
— useful for seeing real recording behavior before real hardware exists.

## Manifest API v5

See `plugin-recording-architecture.md` for the full manifest contract and the
`driver.py`/subprocess dispatch model. Every manifest includes identity,
version, category, entry point, a `runtime` block (`entrypoint`, `protocol`,
`interactive_stdin`, `modes`), UI metadata, settings schemas, capabilities,
polling/timeout policy, clock domain, and backpressure policy. A stream
provider also declares stable source IDs, channels, types, units, nominal
rates, and timestamp origin.

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
- `runtime_modes` (optional platform-mode support; renamed from `readiness` in
  api_version 5 to stop colliding with the unrelated `readiness_requirements`)
- `health` (gates whether the coordinator polls this plugin's status at all;
  declare it only if there is something worth polling)
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

`tools/plugin_sdk.py new sensors <key>` scaffolds a starting manifest and
plugin.py that already pass `validate`/`check-runtime`; steps 2-8 below still
need real, sensor-specific work.

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

## Adding A Card Type

A card extension is one trusted directory under
`software/study_runner/plugins/cards/<plugin_key>/`. Adding one must not add
a named import, type list or special branch to the server, validation entry
points or participant controller. Use a snake_case plugin key; question-type
identifiers may retain their established spelling.

`tools/plugin_sdk.py new cards <key>` scaffolds these four files with a
working placeholder question type; rename the type (see the template's own
comments) since it must be globally unique across every card.

Create these four files:

1. `manifest.json` declares API version 5, category `card`, the runtime driver,
   operation timeouts, UI order, `ui.extensions.card`, and `card.js` as an
   asset. Its versioned `capabilities.card_contract` lists `question_types`,
   `answerless_types`, and any explicit `host_data`. Duplicate question-type
   providers make both providers invalid.
2. `driver.py` calls `run_plugin_driver("<plugin_key>")`.
3. `plugin.py` implements `get_card_defaults`, `normalize_card_config`, and,
   for answerable types, `validate_card_answer`. Keep these operations
   stateless. Import shared rules from `contracts`; do not import runtime
   services or initialize hardware. Defaults returned here are authoritative.
4. `card.js` exports `metaByType`, `configureCard`, `renderStudy`,
   `renderEditor`, `collectConfig`, and `collectAnswer`. Optional
   `isAnswered`, `bindInteractions`, `onInput`, and `onClick` hooks are
   dispatched generically. Store defaults received by `configureCard()` and use
   them for every fallback; do not author a second JavaScript default object.

Add a golden fixture in `tests/support/card_type_fixtures.py` with a realistic
question, frozen normalized config, and a submitted/frozen answer unless the
type is answerless. Update the expected built-in extension-key test when adding
a shipped built-in folder. A temporary or external test extension needs no core
registration.

The editor waits for an extension module and its Python defaults before it
enables that picker option. The participant page loads the cards required by
the study before rendering. A broken required card blocks the flow with a clear
message; a broken unrelated card does not. Python workers isolate backend card
failures, but loaded `card.js` files execute together in the browser, so card
extensions must remain trusted code shipped with the application.

## Camera, Destinations, And Infrastructure

`camera_emotion` is one public plugin. Camera capture and local/remote emotion
workers are internal modes. Notion and Nextcloud are destination plugins and
may hide from dashboard/settings-hub device lists while remaining visible in
study destination settings and finalization. XDF, markers, and clock diagnostics
are recording infrastructure and do not get user-facing plugin menus.

## Source Recording Setup

The platform installer already installs the tested native XDF core of the
matching release; see [Install and start](../README.md#install-and-start). When
you change `software/recording_worker/native/` or only want to rebuild the core,
run:

```bash
python tools/setup_recording_worker.py
```

The command checks CMake and the native compiler (Apple Command Line Tools or
Xcode on macOS, Visual Studio C++ Build Tools on Windows), builds only the
current platform, runs CTest, and performs a synthetic writer smoke test. It
installs nothing automatically. `bash tools/install-macos.sh
--build-core-from-source` and `.\tools\install-windows.cmd -BuildCoreFromSource`
do the same inside the normal installer. Generated output belongs below `software/.build/` and
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
