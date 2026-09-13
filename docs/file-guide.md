# File guide - what every source file does

One line per source file: path, purpose, and whether a non-coder can
edit it safely. **Rule: when you add a source file, add a line here** -
a test (`software/tests/test_file_guide.py`) fails if a `.py`/`.js`
file is missing from this guide.

Edit-safety legend:
- **yes** - text/config-like, safe to adjust with care
- **careful** - logic; small changes OK if you run the tests afterwards
- **no** - security- or data-critical; change only with review

## Entry points

| File | Purpose | Edit? |
|---|---|---|
| `software/server.py` | Start Study Runner from source (`python server.py`); also dispatches the detached `--recording-worker`, emotion-worker, BrainBit CLI, and updater modes | careful |
| `software/study_runner/app_server.py` | Stable server import/module entrypoint delegating to `apps/server/application.py` | careful |
| `software/study_runner/apps/server/application.py` | Wires up the Flask server: port check, HTTPS, startup banner, browser open | careful |
| `software/study_runner/version.py` | The single version number of the app | yes |
| `software/study_runner/self_check.py` | `server.py --self-check`: checks the app without starting HTTP or hardware; packaging CI can also name harmless plugin/card keys to prove a real child RPC and declared card asset work in the bundle | careful |
| `tools/study_runner_manager.py` | Standalone Install & Repair Wizard (downloads, verifies, installs releases) | no |
| `tools/setup_recording_worker.py` | One-time source setup: verifies the toolchain, builds only the current native XDF core, runs CTest and the Python/PyXDF smoke test | no |
| `tools/make_timeline_fixture.py` | Writes a synthetic completed session with a real multi-stream XDF, so the timeline can be seen without recording hardware | no |
| `tools/measure_structure.py` | 1.0 rebuild structure ratchet: cross-package import edges, import cycles, lines per package, largest file, checked against `tools/structure_baseline.json` | careful |
| `tools/extension_sdk.py` | Extension SDK: scaffold (`new`), validate, and boot-test (`check-runtime`) a new plugin outside `extensions/`, plus a generated manifest reference (`schema`) — see `tools/extension_templates/` | careful |
| `tools/synthetic_lsl_source.py` | Pushes fake-but-plausible LSL samples for a sensor extension's own declared stream, so it can be tested without real hardware | careful |
| `tools/install-windows.ps1` / `tools/install-macos.sh` | Idempotent first-install/repair flows: system prerequisites on request, `.venv`, Python requirements, and verified XDF core | careful |
| `tools/start-windows.ps1` / `tools/start-macos.sh` | Daily source-server launchers that use the repository `.venv` directly | careful |
| `software/constraints/py312-*.txt` | Bounded release-tested Python 3.12 compatibility pins: bootstrap, common runtime, and platform-selected local emotion stack | careful |

## Contracts (`software/study_runner/contracts/`)

| File | Purpose | Edit? |
|---|---|---|
| `software/study_runner/contracts/manifest.py` | Pure manifest/command-payload validation and API versions; imports only the standard library, shared by recorder and plugin framework | no |
| `software/study_runner/contracts/plugin_api.py` | Dependency-light runtime context and plugin protocol shared by server, framework, and extension subprocesses | no |
| `software/study_runner/contracts/stream_contract.py` | Turns one manifest-declared stream contract into `desc/study_runner` XDF header fields; every LSL-producing module calls it before creating its outlet | no |
| `software/study_runner/contracts/session_lifecycle.py` | The one explicit session state (`IDLE`/`PREFLIGHT`/`RECORDING`/`FINALIZING`/`SEALED`/`WITHDRAWN`/`FAILED`), derived from the recording plan and finalization job that own the detail, plus the allowed transitions | no |
| `software/study_runner/contracts/recording_checkpoint.py` | The confirmed-prefix contract: how far a segment is *known* to be on disk, written by the worker after each durable flush and read by the host during recovery to name the unconfirmed tail | careful |
| `software/study_runner/contracts/quality_journal.py` | Streaming quality counters (gaps, timestamp regressions, jitter, effective rate), wall-clock jump detection, ingest-backlog monitoring against the transport's bounded buffer, and the versioned quality profile whose thresholds turn a number into an event | careful |
| `software/study_runner/contracts/card_validation_primitives.py` | `CardValidationError` and the small `_normalize_*`/`_require_*` input primitives every card extension's driver imports; card semantics themselves live in the extensions, not here | no |
| `software/study_runner/contracts/card_options.py` | Shared options-list normalization for choice/single/ranking cards | no |

## Apps server - HTTP routes (`software/study_runner/apps/server/routes/`)

| File | Purpose | Edit? |
|---|---|---|
| `routes/__init__.py` | Registers all route groups; ValidationError -> 400 handler | careful |
| `routes/pages.py` | Serves the three pages: `/` (participant), `/admin`, `/audit` | careful |
| `routes/study.py` | Everything the tablet calls: config, sessions, triggers, heartbeat, clock sync | careful |
| `routes/results.py` | Saving results - crash-safe, with recovery files and partial snapshots | no |
| `routes/admin.py` | Operator endpoints: health, studies list/activate/delete, status, restart | careful |
| `routes/sensors.py` | Hardware config, sensor start/stop/restart, camera frames, worker repair | careful |
| `routes/update.py` | In-app updater endpoints (check/download/install) | no |
| `routes/notion.py` | Notion status and offline-queue flush | careful |
| `routes/sessions.py` | Read-only completed-session list, detail, and timeline-signal APIs | careful |
| `routes/certificate.py` | Certificate status plus guarded root-CA export/import endpoints | no |
| `routes/branding.py` | Uploads, removes, and serves the operator's group and funder logos | no |
| `routes/uploads.py` | Background-upload status/retry and validated result-folder opening | no |
| `routes/recovery.py` | Lists crash-orphaned sessions and finalizes or discards them | no |
| `routes/finalization.py` | Read-only status plus guarded retry, degraded-confirmation, and exact-session-folder actions for durable finalization jobs | no |
| `routes/plugins.py` | Serves the manifest-derived plugin catalog used by generic admin UI | careful |
| `routes/helpers.py` | Shared request helpers: runtime config, sessions, sensor runtime | careful |

## Runtime and shared services (`software/study_runner/runtime_core/`, `software/study_runner/shared/`)

| File | Purpose | Edit? |
|---|---|---|
| `software/study_runner/runtime_core/studies/__init__.py` | Empty package marker | yes |
| `software/study_runner/shared/atomic_io.py` | Crash-safe JSON writes (temp file + replace) for all study data | no |
| `software/study_runner/shared/software_root.py` | Locates `software/` by marker (`server.py` + `study_runner/`) instead of counting parent levels | no |
| `software/study_runner/shared/runtime_mode.py` | `is_frozen`/`get_app_mode`/`get_project_base_dir` shared by extensions, plugin framework, and runtime settings | no |
| `software/study_runner/shared/study_identifiers.py` | `normalize_study_id` -- the one place a study's stable filename/credential key gets computed; re-exported from `study_config_service.py` | no |
| `software/study_runner/shared/dependency_utils.py` | `ensure_requirements` -- shared by `data_core/host` and plugin framework without creating an area dependency | no |
| `software/study_runner/contracts/participant_fields.py` | Canonical participant-field order, defaults, and configurable options shared by study validation, the participant-id card, and destination extensions | no |
| `software/study_runner/shared/filename_sanitizer.py` | `sanitize_identifier_for_filename` -- needed by both `runtime_core/studies` (results/recovery) and `data_core/host` (sensor flush), so it belongs to neither | no |
| `software/study_runner/data_core/contract/recording_errors.py` | Typed recording/worker errors shared across the host/worker boundary | no |
| `software/study_runner/data_core/contract/worker_protocol.py` | The authenticated, idempotent loopback wire protocol between host and worker | careful |
| `software/study_runner/data_core/contract/backup_projection.py` | Slowest-grid backup projection model (`BackupProjection`/`BackupSampler`) shared by host and worker | careful |
| `software/study_runner/data_core/contract/recording_lease.py` | The persistent 15-minute recording lease shared by host and worker | careful |
| `software/study_runner/data_core/contract/native_core_probe.py` | `CoreProbe`/`probe_core_library` lets the host validate a build without depending on the worker | careful |
| `software/study_runner/data_core/contract/lsl_dependency.py` | `require_pylsl`/`lsl_version_info` shared by host preflight and worker inlet setup | no |
| `software/study_runner/shared/system_clock_probe.py` | Package 5b preflight: system-clock plausibility bounds plus a per-platform time-sync-service check; no network access | careful |
| `software/study_runner/data_core/host/recording_capacity.py` | Package 5b preflight: predicts required storage from the negotiated recording contract's declared stream rates (never disk throughput) against the study's planned duration | careful |
| `software/study_runner/runtime_core/studies/validation.py` | Validates study configs and submitted results (has a TOC docstring) | careful |
| `software/study_runner/runtime_core/studies/card_extension_bridge.py` | Package 5g.B5: dispatches `card_defaults`/`card_normalize`/`card_validate_answer` to the owning card extension's process, resolves host-supplied data (e.g. stimulus's `plugin_actions`), and turns a card process fault into `CardExtensionUnavailableError` (503) vs. an invalid answer into `CardValidationError` (400) | careful |
| `software/study_runner/runtime_core/studies/results_service.py` | Builds answer details, slices biosignals per card, writes result files | no |
| `software/study_runner/runtime_core/studies/sessions_index_service.py` | Scans completed results and builds bounded timeline envelopes | careful |
| `software/study_runner/runtime_core/studies/session_quality_summary.py` | Reduces quality.jsonl (5c) and its unconfirmed-tail entries (5h) to a UI-sized health level and structured findings | careful |
| `software/study_runner/runtime_core/studies/session_store.py` | Persistent, rehydrating registry of active tablet study sessions | no |
| `software/study_runner/data_core/host/sensor_flush_service.py` | Periodic background export of live sensor history for crash recovery | no |
| `software/study_runner/data_core/host/sensor_coordinator_service.py` | Central plugin lifecycle/status wrapper with manifest, backpressure, and timing diagnostics | careful |
| `software/study_runner/data_core/host/clock_sync_service.py` | Bounded tablet/worker offset and RTT histories for timing diagnostics | careful |
| `software/study_runner/runtime_core/studies/recovery_service.py` | Finds crash-orphaned sessions and finalizes or discards them | no |
| `software/study_runner/runtime_core/settings/update_service.py` | In-app updater: manifest fetch, signature check, download, staging | no |
| `software/study_runner/runtime_core/settings/ssl_service.py` | Local HTTPS certificate authority for tablet camera access | no |
| `software/study_runner/runtime_core/delivery/certificate_download_service.py` | Plain-HTTP, one-file bootstrap download for the local root CA | careful |
| `software/study_runner/runtime_core/delivery/certificate_transfer_service.py` | Validates, exports, and transactionally imports the reusable local root CA | no |
| `software/study_runner/runtime_core/settings/branding_service.py` | Validates logo uploads and resolves a slot to a stored file, never to a caller's path | no |
| `software/study_runner/runtime_core/delivery/withdrawal_service.py` | Consent withdrawal: stops writers, cancels pending uploads, deletes the session tree and the journal copies outside it, then leaves a `WITHDRAWN.json` tombstone; its ledger lives outside the folder it empties so an interrupted run can resume | dangerous |
| `software/study_runner/runtime_core/delivery/upload_jobs_service.py` | Persistent upload journal, crash replay, backoff worker, and retry state | no |
| `software/study_runner/runtime_core/delivery/upload_runtime.py` | Registers manifest-declared destination plugin handlers with persistent upload jobs | no |
| `software/study_runner/runtime_core/settings/folder_open_service.py` | Validates and opens result folders on Windows or macOS | no |
| `software/study_runner/runtime_core/settings/runtime_config.py` | Paths, ports, app mode, data-folder resolution | careful |
| `software/study_runner/runtime_core/studies/study_config_service.py` | Load/save the active study and the saved-studies folder | careful |
| `software/study_runner/runtime_core/studies/study_run_state_service.py` | Persists the operator-controlled loaded/running/completed run state | careful |
| `software/study_runner/data_core/host/study_sensor_runtime.py` | Which sensors are effectively on (study settings + overrides) | careful |
| `software/study_runner/runtime_core/studies/trial_service.py` | Sends stimulus start/stop markers to plugins and the two built-in recording sources | careful |
| `software/study_runner/runtime_core/studies/study_client_service.py` | Tablet heartbeat bookkeeping | careful |
| `software/study_runner/runtime_core/settings/secrets_service.py` | Local-secrets file I/O plus manifest-driven hardware-config redaction | no |
| `software/study_runner/plugin_framework/plugin_secrets.py` | Per-study credential overrides and secret resolution, never written into the exported study | no |
| `software/study_runner/runtime_core/studies/study_readiness_service.py` | Pre-run check: what would stop the loaded study from delivering results | careful |
| `software/study_runner/runtime_core/settings/plugin_settings_service.py` | Manifest-driven machine settings: schema, validation, targeted deep-merge writes | no |
| `software/study_runner/runtime_core/settings/hardware_settings_service.py` | Saves hardware_settings.json | careful |
| `software/study_runner/runtime_core/settings/shortcut_service.py` | Creates the desktop shortcut | careful |
| `software/study_runner/runtime_core/settings/admin_status_service.py` | Aggregates plugin status for the dashboard | careful |
| `software/study_runner/runtime_core/delivery/artifact_manifest_service.py` | Owns artifact provenance, checksums, completion markers, and guarded source purge | no |
| `software/study_runner/runtime_core/studies/card_summary_service.py` | Pure merged-XDF-to-card-statistics derivation | no |
| `software/study_runner/runtime_core/delivery/finalization_runtime.py` | Wires the persistent finalizer to recording and upload adapters | no |
| `software/study_runner/runtime_core/delivery/finalization_service.py` | Durable, idempotent session-finalization state machine and journal replay | no |
| `software/study_runner/runtime_core/delivery/destination_plugin_service.py` | Converts upload-destination manifests into persisted finalization steps and recovery/purge policies | no |
| `software/study_runner/data_core/host/plugin_health_poll_service.py` | Manifest-paced, non-blocking per-plugin health cache and bounded poll executor | careful |
| `software/study_runner/data_core/host/recording_runtime.py` | Public compatibility facade plus session-level recording orchestration; contains no process-launch or scientific-validation implementation | no |
| `software/study_runner/data_core/host/recording_dependencies.py` | liblsl dependency probe and capability-based selection of study/internal recording providers | no |
| `software/study_runner/data_core/host/recording_worker_launcher.py` | Detached Python/self worker process command, isolation flags, and startup health handshake | no |
| `software/study_runner/data_core/host/recording_runtime_support.py` | Shared recording error, constants, safe session-path/JSON helpers, recovery grid, and required-source readiness gate | no |
| `software/study_runner/data_core/host/recording_quality.py` | Scientific source, backup, gap/drop, lease-expiry, and finalization quality checks | no |
| `software/study_runner/runtime_core/delivery/recording_finalization_adapter.py` | Thin bridge from persistent finalization steps to recording freeze, validation, merge, and shutdown | no |
| `software/study_runner/runtime_core/studies/study_plugin_config.py` | Migrates legacy sensor/upload/card fields into the manifest-driven plugin settings shape | careful |
| `software/study_runner/runtime_core/studies/trial_event_service.py` | Persists idempotent trial events and backend-enforced deadlines | no |
| `software/study_runner/runtime_core/studies/session_journal_service.py` | Append-only, fsynced per-session audit journals plus terminal-finalization archive | no |
| `software/study_runner/data_core/host/recording_contract.py` | Builds and persists the immutable, hash-checked `recording-plan.json` snapshot each session starts with | no |

## Recording, host side (`software/study_runner/data_core/host/`)

| File | Purpose | Edit? |
|---|---|---|
| `artifacts.py` | Canonical immutable session identity, path layout, and SHA-256 helpers | no |
| `backup.py` | Validates slowest-grid backup projections and stale-value rules | no |
| `coordinator.py` | Allocates append-never plugin segments and sends idempotent worker commands | no |
| `errors.py` | Shared typed recording errors used across coordinator, worker client, and finalization | no |
| `recovery.py` | Detects recoverable recording state and allocates append-never post-crash segments | no |
| `worker_binary.py` | Locates and validates the tiny platform-native XDF core and its build manifest | no |
| `worker_protocol.py` | Authenticated loopback protocol, persisted endpoint state, and command replay ledger | no |
| `xdf.py` | Native-worker backend contract plus pinned PyXDF source/merge validation | no |
| `markers.py` | The study's own event-marker LSL outlet; every session carries it, not a plugin | careful |
| `clock_diagnostics.py` | Wall/LSL/client clock observations at event boundaries; every session carries it, not a plugin | careful |
| `markers.manifest.json`, `clock_diagnostics.manifest.json` | Declare each built-in source's streams the same way a plugin manifest does, loaded through the same validator, never discovered from a directory | no |

## Detached recording worker (`software/study_runner/data_core/worker/`)

| File | Purpose | Edit? |
|---|---|---|
| `application.py` | Authenticated loopback command server with leases, generation fencing, and durable command replay | no |
| `core.py` | Checked `ctypes` binding for the stable C ABI exposed by `xdf_core` | no |
| `lsl_recording.py` | Threaded LSL inlet capture, validated stream headers, bounded drain, and backup projection | no |
| `session_journals.py` | Appends `quality.jsonl`/`timing.jsonl` while recording and fsyncs them on the same checkpoint tick as the XDF data, so an aborted session still leaves quality evidence | no |
| `runtime.py` | Session lifecycle, segment control, merge orchestration, and worker health state | no |

The audited native XDF writer lives in `software/recording_worker/native/`.
Its CMake target wraps the pinned LabRecorder XDFWriter behind a small C ABI;
it deliberately contains no HTTP, LSL, plugin, or study logic.

## Updater trust chain

| File | Purpose | Edit? |
|---|---|---|
| `software/study_runner/updates/signatures.py` | THE shared signed-update wire format + Ed25519 verification | no |
| `software/study_runner/updates/trusted_keys.py` | Trusted public keys (filled in by CI at release build) | no |
| `software/study_runner/updates/installer.py` | Applies a staged update on restart (`--apply-update`) | no |

## Plugins (`software/study_runner/extensions/`)

The folder name, public plugin key, and hardware-config key are deliberately
not assumed to be identical. `test_plugin_registry.py` freezes this compatibility
mapping:

| Folder | Plugin key | Config key |
|---|---|---|
| `brainbit` | `brainbit` | `brainbit` |
| `mr60_mini_radar` | `mini_radar` | `mini_radar` |
| `camera_emotion` | `camera_emotion` | `camera_emotion` |
| `osc_touchdesigner` | `osc` | `osc` |
| `notion_upload` | `notion` | `notion` |
| `nextcloud_upload` | `nextcloud` | `nextcloud` |

`lsl_markers` and `clock_diagnostics` used to be here. Removing either broke
recording -- see `tests/test_plugin_removability.py` -- so they are core
recording code now, not extensions: `data_core/host/markers.py` and
`data_core/host/clock_diagnostics.py`.

| File | Purpose | Edit? |
|---|---|---|
| `plugin_secrets.py` | Per-study credential storage and env/study/machine/legacy resolution used by the host and each extension subprocess | careful |
| `adapter_utils.py` | Shared timestamps, locked state updates, and config-section lookup | careful |
| `registry.py` | Manifest-driven plugin lookup, generic actions, interval summaries, and sidecar exports | careful |
| `plugin_catalog.py` | Discovers trusted extension folders and validates API-v4 manifests before dispatch | no |
| `extension_layout.py` | Defines trusted extension category roots shared by discovery, drivers, UI assets, and self-check | no |
| `driver_runtime.py` | Runtime used by the single `driver.py` entry point every API-v4 plugin process runs | careful |
| `process_host.py` | Host-side supervisor for API-v4 drivers: start/stop/restart, line-oriented console, reserved-prefix RPC; cards additionally get no app context and terminate their process on an RPC timeout, everyone else does not | careful |
| `card_catalog.py` | Package 5g.B5: `card_bindings()` -- the manifest-driven `question_type -> (catalog entry, card_contract)` lookup every card-aware caller reads instead of a hardcoded type list; `QuestionTypes` is the live set view `ALLOWED_QUESTION_TYPES`/`NON_ANSWER_QUESTION_TYPES` wrap | careful |
| `history_buffer.py` | Session-sized ring buffers + gap/truncation detection for all sensors | careful |
| `dependency_utils.py` | Optional auto-install of Python packages sensors need | careful |
| `__init__.py` (all) | Empty package markers | yes |
| `brainbit/adapter.py` | Supervises the BrainBit CLI process (has a TOC docstring) | careful |
| `brainbit/brainbit_realtime_cli.py` | The external BrainBit EEG CLI itself (SOURCE OF TRUTH; in a lab-workspace checkout, `../../Sensorik/` is the separate external hardware/sensor reference folder this mirrors into — see `docs/README.md` — not generated by the app and not part of this repo); also runs inside packaged builds via `--brainbit-cli`. See `extensions/sensors/brainbit/README.md` for NeuroSDK/BrainFlow provenance. | careful |
| `brainbit/plugin.py` | Plugin wrapper: config defaults + lifecycle for BrainBit | careful |
| `brainbit/driver.py` | API-v4 process entry point (`run_plugin_driver("brainbit")`) | no |
| `brainbit/diagnose_backends.py` | Standalone 30-second NeuroSDK vs. BrainFlow A/B diagnostic, outside the acquisition path | careful |
| `mr60_mini_radar/adapter.py` | MR60 heart/breathing radar via serial or BLE, with auto-reconnect | careful |
| `mr60_mini_radar/plugin.py` | Plugin wrapper for the radar | careful |
| `mr60_mini_radar/driver.py` | API-v4 process entry point (`run_plugin_driver("mini_radar")`) | no |
| `mr60_mini_radar/tools/ble_mr60_receiver.py` | Standalone BLE test receiver for debugging | yes |
| `camera_emotion/adapter.py` | Accepts tablet camera frames and publishes stable LSL streams | careful |
| `camera_emotion/plugin.py` | Single public camera/emotion plugin and generic admin actions | careful |
| `camera_emotion/driver.py` | API-v4 process entry point (`run_plugin_driver("camera_emotion")`) | no |
| `camera_emotion/worker/server.py` | Internal DeepFace worker process (Flask, `--emotion-worker`) | careful |
| `camera_emotion/worker/plugin.py` | Starts, monitors, and repairs the internal worker | careful |
| `camera_emotion/worker/analyzer.py` | Runs DeepFace on one frame | careful |
| `camera_emotion/worker/model_errors.py` | Shared DeepFace error classification + suggested fixes | careful |
| `osc_touchdesigner/adapter.py` + `plugin.py` | Forwards values to TouchDesigner via OSC | careful |
| `osc_touchdesigner/driver.py` | API-v4 process entry point (`run_plugin_driver("osc")`) | no |
| `notion_upload/adapter.py` + `plugin.py` | Uploads result summaries to Notion (with offline queue) | careful |
| `notion_upload/driver.py` | API-v4 process entry point (`run_plugin_driver("notion")`) | no |
| `nextcloud_upload/plugin.py` | Declares the hidden Nextcloud destination capability, publishes, and validates its own share-link setting | careful |
| `nextcloud_upload/webdav_client.py` | The WebDAV client: uploads session files to a writable Nextcloud public share, checksum-first | careful |
| `nextcloud_upload/driver.py` | API-v4 process entry point (`run_plugin_driver("nextcloud")`) | no |

## Cards (`software/study_runner/extensions/cards/`)

Package 5g.B5: every question/card type is a genuine, process-isolated API-v4
plugin like any sensor or destination above, not a hardcoded type string.
`participant-id`/`finish` are ordinary card extensions too -- but the
mandatory bookends of every study, so a catalog with neither cannot author a
*playable* study (see `tests/test_plugin_removability.py`'s module docstring
for why that is accepted, not a regression).

Twelve folders, one per extension (`choice` alone answers both `choice` and
`single`); each one is the same four files:

| File | Purpose | Edit? |
|---|---|---|
| `<extension>/manifest.json` | Declares `capabilities.card_contract`: `question_types`, `answerless_types`, and any `host_data` the extension needs from the host (e.g. stimulus's `plugin_actions`); `ui.extensions.card` names its JS entry point | careful |
| `<extension>/driver.py` | API-v4 process entry point (`run_plugin_driver("<extension>")`) | no |
| `<extension>/plugin.py` | Implements the executable card contract: `get_card_defaults`/`normalize_card_config`/`validate_card_answer`; imports only `contracts`, never `runtime_core` | careful |
| `<extension>/card.js` | Renderer/editor module: `metaByType`, `configureCard`, `renderStudy`, `renderEditor`, `collectConfig`, `collectAnswer`, and the optional `isAnswered`/`bindInteractions` hooks; loaded on demand by `apps/ui/scripts/cards/index.js` | careful |

Folders: `choice`, `finish`, `likert`, `mood_meter`, `multi_slider`,
`participant_id`, `ranking`, `semantic`, `slider`, `stimulus`, `text`,
`word_cloud` (`test_plugin_registry.py`'s `EXPECTED_CARD_PLUGIN_KEYS` freezes
this list; folder, plugin key, and config key are the same string for every
one, unlike the plugin table above).

## Frontend (`software/study_runner/apps/ui/scripts/`)

| File | Purpose | Edit? |
|---|---|---|
| `participant/study-controller.js` | The participant flow engine: cards, navigation, snapshots, submit, preview mode | careful |
| `admin/admin-controller.js` | Study editor, save/load, QR codes, updates | careful |
| `admin/admin-dashboard-controller.js` | Live sensor dashboard with plain-language statuses | careful |
| `settings/machine/machine-settings-panel.js` | Machine settings shell: nav, generated sensor forms, tablet links | careful |
| `settings/machine/certificate-settings-controller.js` | Certificate status, setup, export, and import, inside the machine settings shell | no |
| `settings/machine/branding-settings-controller.js` | Upload and remove the group and funder logos, inside the machine settings shell | no |
| `settings/study/study-settings-panel.js` | Per-study settings shell (editor only): sensors, participant, uploads, export | careful |
| `settings/study/notion-settings-controller.js` | Notion fields inside the per-study settings panel | careful |
| `settings/study/nextcloud-settings-controller.js` | Nextcloud fields inside the per-study settings panel | careful |
| `admin/session-timeline.js` | Renders completed-session sensor lanes and answer markers as offline SVG | careful |
| `admin/sessions-browser.js` | Completed-session hub list, detail panel, and timeline data fetching | careful |
| `admin/upload-monitor.js` | Background-upload completion modal and the corner progress widget it shrinks to | careful |
| `admin/finalization-monitor-view.js` | Generic finalization modal/widget renderer and guarded operator actions | careful |
| `admin/recovery-panel.js` | Hub banner listing crash-orphaned sessions, with finalize/discard actions | careful |
| `admin/plugin-console.js` | Diagnostics modal: guided plugin status view plus the line-oriented expert console (SSE-fed) | careful |
| `shared/study-settings.js` | THE client-side study-settings shape; mirrors `_validate_study_settings` | no |
| `shared/deadline-timer.js` | Monotonic deadline timer whose UI ticks never define elapsed study time | no |
| `shared/finalization-view-model.js` | Pure finalization status/progress view model | careful |
| `shared/timeline-view-model.js` | Pure timeline model: stream grouping, waveform/line classification from the LSL header, zoom window maths | careful |
| `shared/plugin-catalog.js` | Fetches and indexes manifest-derived plugin UI capabilities | careful |
| `shared/participant-plugin-extensions.js` | Failure-isolated lifecycle manager for manifest-declared participant extensions | careful |
| `shared/reliable-event-queue.js` | Session-local idempotent marker/event buffering and retry | no |
| `shared/view-transition.js` | Full-screen sweep between admin views; swaps the view while covered | careful |
| `shared/settings-shell.js` | Shared left-nav/right-panel wiring for both settings surfaces | careful |
| `shared/dom-utils.js` | Shared safe DOM lookup, text/HTML assignment, and escaping helpers | careful |
| `shared/modal.js` | Shared accessible modal lifecycle, modal-shell markup, and the yes/no confirmation | careful |
| `shared/branding.js` | Shared branding fetch and logo rendering for the waiting slide and the hub | careful |
| `shared/ambient-bubbles.js` | Self-contained morphing background for the waiting slide; tune CONFIG at the top | no |
| `shared/settings-page.js` | Shared navigation, setup-step state, and action feedback for settings pages | careful |
| `shared/api-client.js` | Tiny fetch helpers (getJson/postJson) | careful |
| `shared/i18n.js` | Translation loading and the `t()` helper | careful |
| `extensions/sensors/camera_emotion/ui/participant.js` | Camera/emotion participant lifecycle extension for preview, stimuli, submit, and heartbeat status | careful |
| `extensions/sensors/camera_emotion/ui/camera-capture.js` | Plugin-owned tablet camera capture and frame upload adapter | careful |
| `extensions/sensors/brainbit/ui/dashboard.js` | Optional BrainBit rich-status renderer loaded through the manifest extension hook | careful |
| `extensions/sensors/mr60_mini_radar/ui/dashboard.js` | Optional MR60 rich-status renderer loaded through the manifest extension hook | careful |
| `extensions/sensors/camera_emotion/ui/dashboard.js` | Optional camera/emotion rich-status renderer loaded through the manifest extension hook | careful |
| `participant/study-client-heartbeat.js` | Keeps the tablet visible on the dashboard | careful |
| `shared/qr-code.js` | QR code rendering for the access card | no |
| `cards/index.js` | Package 5g.B5: `loadCards()` fetches each installed card's `card.js` (`/api/plugins/<key>/assets/card.js`) and Python-authoritative defaults (`/api/plugins/<key>/card-defaults`) instead of a static import list; the 12 card modules themselves now live in `extensions/cards/<name>/card.js` | careful |
| `cards/card-info.js` | The shared editor frame every card composes into: question text, instruction, note, toggle group | careful |

Locales (`apps/ui/locales/en.json`, `de.json`) hold every UI string; both
files must have identical keys (a test checks this). `web/vendor/`
holds offline copies of third-party assets (icons).

## Release tooling (`release_tools/`, repo root)

| File | Purpose | Edit? |
|---|---|---|
| `release_tools/build_source_release.py` | Builds and verifies the source-release archives, checksums, metadata, and release notes | careful |
| `release_tools/tests/test_build_source_release.py` | Regression tests for safe source archives, metadata, release notes, and workflow contracts | yes |
| `release_tools/build_python_onedir.py` | Legacy/future non-recording experiment: runs PyInstaller; not used by the active release | careful |
| `release_tools/package_python_onedir.py` | Legacy/future non-recording experiment: packages PyInstaller output; not used by the active release | careful |
| `release_tools/build_python_update_manifest.py` | Legacy packaged-updater manifest signer; not used by the active release | no |
| `release_tools/write_python_update_key.py` | Legacy packaged-updater key helper; not used by the active release | no |
| `release_tools/fetch_deepface_model_assets.py` | Explicitly provisions the separately licensed, SHA-256-pinned DeepFace emotion model after terms acceptance | careful |
| `release_tools/build_offline_wheelhouse.py` | Builds an offline pip wheelhouse | careful |
| `release_tools/pyinstaller/study_runner_server_common.py` | Legacy/future PyInstaller spec pieces; not used by the active source release | careful |
| `release_tools/tests/test_pyinstaller_common.py` | Regression tests for the frontend runtime path and dynamic plugin-bundle collection | yes |
| `release_tools/release-study-runner.mjs` | The release script: bump, check, tag, push (run via release.ps1) | no |
| `release_tools/verify-release-version.mjs` | CI guard: tag matches version.py | no |

Tests live in `software/tests/` - one file per area, named
`test_<area>.py`. Run everything with:
`python -m unittest discover software/tests`

`software/tests/test_source_install_scripts.py` protects the WinGet/Homebrew,
`.venv`, canonical-core, non-destructive installer, and daily-start contracts.
`software/tests/test_python_constraints.py` protects the exact scientific pins,
platform split, shared installer/CI consumption, and honest non-lockfile scope.
