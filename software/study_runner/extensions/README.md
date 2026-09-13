# Built-in extensions

Each trusted plugin shipped with Study Runner owns one directory containing:

```text
extensions/<category>/<folder>/
  manifest.json
  plugin.py
  optional adapter.py, ui/*.js, assets, and focused helpers
```

The trusted category roots are `sensors`, `cards`, `destinations`, and
`outputs`. The manifest remains authoritative for runtime capabilities and its
stable `plugin_key`; the directory category controls code ownership and import
boundaries. Discovery combines every root before checking global uniqueness.
The framework lives in `../plugin_framework/`.

The server discovers these folders automatically. There is no central import
list and no web upload or dependency installer for plugins. A malformed
manifest, duplicate plugin key, duplicate stream source ID, or incompatible
handler is reported as `invalid` by `GET /api/plugins/catalog`; it does not
stop other plugins or the server from loading.

## Manifest contract

Every current manifest uses `api_version: 5` and declares identity, plugin version,
category, config key, `plugin:PLUGIN` entry point, UI metadata, settings
schemas, timing limits, and capabilities. Important capability names are:

- `study_sensor`: selectable for a study, required by default when selected.
- `lsl_stream_provider`: owns stable stream/source IDs and channel metadata.
- `recording_source`: contributes native XDF segments.
- `backup_projection`: declares numeric channels and a positive projection rate.
- `acquisition_transport`: declares how samples reach LSL; browser sources also
  guarantee heartbeat, sequence, and source timestamps.
- `runtime_modes`, `health`, and `admin_actions`: lifecycle, diagnostics, and
  generic manifest-declared operator actions. `health` gates whether the
  admin coordinator polls this plugin's status at all -- declare it only if
  there's something worth polling. `runtime_modes` (renamed from `readiness`
  in api_version 5; `runtime_control` was retired the same release, both
  traced to have zero effect before the change) declares optional
  platform-mode support.
- `readiness_requirements`: distinct from `runtime_modes` above - what a study
  needs before this plugin can actually deliver results:
  `requires_secret`, `requires_settings` (a list; any one satisfies it), and
  `requires_machine_enabled`. `runtime_core/studies/study_readiness_service.py` checks every
  plugin that declares this the same way, before the study can start.
- `participant_actions` and `participant_ingest`: closed allow-lists for
  participant lifecycle commands and browser payloads through generic routes.
- `machine_settings`, `study_settings`, and `card_actions`: generic UI schemas.
- `upload_destination`: a background publication target with a required
  `publish_destination(context, payload)` handler.
- `credentials`: declares the one secret field a plugin needs
  (`config_field`, `env_var`, `per_study`) so `context.secret(plugin_key)`
  resolves it env > per-study > machine > legacy config. The manifest never
  carries the value - see `plugin_framework/plugin_secrets.py`.

Only manifests in the application package are trusted. The entry point is
resolved inside its own folder after schema and duplicate checks pass.

Optional rich UI stays inside the plugin. Declare entry modules as
`ui.extensions.dashboard` and/or `ui.extensions.participant`; declare relative
JavaScript imports under `ui.assets`. Discovery rejects absolute, traversing,
missing, or non-JavaScript paths, and the asset endpoint serves only those exact
manifest entries. A failed extension is isolated and the generic UI remains
usable. `ui.timeline.lane_aliases` and `preferred_channels` control completed-
session lanes without adding sensor keys to the renderer.

Admin actions use a closed `payload_schema`. Dynamic buttons may map cached
status candidates with `instances.status_paths`, `payload_map`, and
`label_fields`. The server rejects unknown fields and invalid types before it
calls `run_admin_action(context, action_key, payload)`.

Browser ingest manifests declare acceptable source timestamp fields and the
route also requires a sequence number. Upload destinations declare their queue
key, legacy load-only aliases, and the policies `requires_valid_result`,
`publish_on_attention`, `republish_on_degraded`, and
`purge_verified_sources`. Only one installed destination may grant verified
source purge. Adding a destination needs no upload-runtime or finalization key
change: discovery registers its handler and persists a `publish_<plugin-key>`
step. A destination that needs a secret declares `credentials`, and a
connection to test declares a `test_connection` admin action - both existing
capabilities, not a special case for uploads. The value itself is never in
the manifest, machine settings, or exported study data.

Set `lifecycle.reinitialize_on_disable: true` only when disabling a plugin must
rebuild its inert adapter state. Registry lifecycle behavior is read from this
flag and never from a hard-coded plugin-key set.

## Adding a sensor

`python tools/extension_sdk.py new sensors <plugin_key>` writes and checks
the folder structure above for you; steps 2-5 below are the sensor-specific
work it cannot do.

1. Choose a stable lowercase `plugin_key`.
2. Define LSL streams with unique, stable `source_id` values, nominal rates,
   clock domains, channel types, labels, and units.
3. Add `study_sensor`, `lsl_stream_provider`, `recording_source`, and a valid
   `backup_projection` when the sensor participates in recording.
4. Implement `PLUGIN` with the handlers promised by the manifest.
5. Add a fixture test proving discovery, settings, readiness, recording,
   backup projection, and card statistics without a core registry change.

The former aggregate `plugin_manifests.json` is intentionally gone. Discovery
reads only per-folder manifests. A top-level package without a manifest is
reported as invalid; an intentional internal helper or compatibility shim must
carry an explicit `.pluginignore` marker and is never imported by discovery.

`camera_emotion/` is the single camera plugin and owns its internal
`worker/`. The old `tablet_camera_emotion` and `local_emotion_worker` package
names are one-release import/CLI shims only; neither is a catalog source.
