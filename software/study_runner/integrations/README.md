# Built-in integration plugins

Each trusted plugin shipped with Study Runner owns one directory containing:

```text
integrations/<folder>/
  manifest.json
  plugin.py
  optional adapter.py, ui/*.js, assets, and focused helpers
```

The server discovers these folders automatically. There is no central import
list and no web upload or dependency installer for plugins. A malformed
manifest, duplicate plugin key, duplicate stream source ID, or incompatible
handler is reported as `invalid` by `GET /api/plugins/catalog`; it does not
stop other plugins or the server from loading.

## Manifest contract

Every manifest uses `api_version: 3` and declares identity, plugin version,
category, config key, `plugin:PLUGIN` entry point, UI metadata, settings
schemas, timing limits, and capabilities. Important capability names are:

- `study_sensor`: selectable for a study, required by default when selected.
- `lsl_stream_provider`: owns stable stream/source IDs and channel metadata.
- `recording_source`: contributes native XDF segments.
- `backup_projection`: declares numeric channels and a positive projection rate.
- `acquisition_transport`: declares how samples reach LSL; browser sources also
  guarantee heartbeat, sequence, and source timestamps.
- `readiness`, `runtime_control`, `health`, and `admin_actions`: lifecycle,
  diagnostics, and generic manifest-declared operator actions.
- `participant_actions` and `participant_ingest`: closed allow-lists for
  participant lifecycle commands and browser payloads through generic routes.
- `machine_settings`, `study_settings`, and `card_actions`: generic UI schemas.
- `upload_destination`: a background publication target with a required
  `publish_destination(context, payload)` handler.

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
step. Credentials remain private plugin/secret-store responsibility, never
manifest settings or exported study data.

Set `lifecycle.reinitialize_on_disable: true` only when disabling a plugin must
rebuild its inert adapter state. Registry lifecycle behavior is read from this
flag and never from a hard-coded plugin-key set.

## Adding a sensor

1. Copy the folder structure above and choose a stable lowercase `plugin_key`.
2. Define LSL streams with unique, stable `source_id` values, nominal rates,
   clock domains, channel types, labels, and units.
3. Add `study_sensor`, `lsl_stream_provider`, `recording_source`, and a valid
   `backup_projection` when the sensor participates in recording.
4. Implement `PLUGIN` with the handlers promised by the manifest.
5. Add a fixture test proving discovery, settings, readiness, recording,
   backup projection, and card statistics without a core registry change.

The former aggregate `plugin_manifests.json` is intentionally gone. API v3
reads only per-folder manifests. A top-level package without a manifest is
reported as invalid; an intentional internal helper or compatibility shim must
carry an explicit `.pluginignore` marker and is never imported by discovery.

`camera_emotion/` is the single camera plugin and owns its internal
`worker/`. The old `tablet_camera_emotion` and `local_emotion_worker` package
names are one-release import/CLI shims only; neither is a catalog source.
