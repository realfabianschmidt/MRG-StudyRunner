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
| `software/study_runner/app_server.py` | Wires up the Flask server: port check, HTTPS, startup banner, browser open | careful |
| `software/study_runner/version.py` | The single version number of the app | yes |
| `tools/study_runner_manager.py` | Standalone Install & Repair Wizard (downloads, verifies, installs releases) | no |
| `tools/setup_recording_worker.py` | One-time source setup: verifies the toolchain, builds only the current native XDF core, runs CTest and the Python/PyXDF smoke test | no |
| `tools/make_timeline_fixture.py` | Writes a synthetic completed session with a real multi-stream XDF, so the timeline can be seen without recording hardware | no |
| `tools/install-windows.ps1` / `tools/install-macos.sh` | Idempotent first-install/repair flows: system prerequisites on request, `.venv`, Python requirements, and verified XDF core | careful |
| `tools/start-windows.ps1` / `tools/start-macos.sh` | Daily source-server launchers that use the repository `.venv` directly | careful |
| `software/constraints/py312-*.txt` | Bounded release-tested Python 3.12 compatibility pins: bootstrap, common runtime, and platform-selected local emotion stack | careful |

## Backend - HTTP routes (`software/study_runner/backend/routes/`)

| File | Purpose | Edit? |
|---|---|---|
| `routes/__init__.py` | Registers all route groups; ValidationError -> 400 handler | careful |
| `routes/pages.py` | Serves the three pages: `/` (participant), `/admin`, `/audit` | careful |
| `routes/study.py` | Everything the tablet calls: config, sessions, triggers, heartbeat, clock sync | careful |
| `routes/results.py` | Saving results - crash-safe, with recovery files and partial snapshots | no |
| `routes/admin.py` | Operator endpoints: health, studies list/activate/delete, status, restart | careful |
| `routes/sensors.py` | Hardware config, sensor start/stop/restart, camera frames, worker repair | careful |
| `routes/update.py` | In-app updater endpoints (check/download/install) | no |
| `routes/notion.py` | Notion status, queue flush, connection test | careful |
| `routes/nextcloud.py` | Tests a study's writable Nextcloud public-share connection | careful |
| `routes/sessions.py` | Read-only completed-session list, detail, and timeline-signal APIs | careful |
| `routes/certificate.py` | Certificate status plus guarded root-CA export/import endpoints | no |
| `routes/branding.py` | Uploads, removes, and serves the operator's group and funder logos | no |
| `routes/uploads.py` | Background-upload status/retry and validated result-folder opening | no |
| `routes/recovery.py` | Lists crash-orphaned sessions and finalizes or discards them | no |
| `routes/finalization.py` | Read-only status plus guarded retry, degraded-confirmation, and exact-session-folder actions for durable finalization jobs | no |
| `routes/plugins.py` | Serves the manifest-derived plugin catalog used by generic admin UI | careful |
| `routes/helpers.py` | Shared request helpers: runtime config, sessions, sensor runtime | careful |

## Backend - services (`software/study_runner/backend/services/`)

| File | Purpose | Edit? |
|---|---|---|
| `services/__init__.py` | Empty package marker | yes |
| `services/atomic_io.py` | Crash-safe JSON writes (temp file + replace) for all study data | no |
| `services/validation.py` | Validates study configs and submitted results (has a TOC docstring) | careful |
| `services/results_service.py` | Builds answer details, slices biosignals per card, writes result files | no |
| `services/sessions_index_service.py` | Scans completed results and builds bounded timeline envelopes | careful |
| `services/session_store.py` | Persistent, rehydrating registry of active tablet study sessions | no |
| `services/sensor_flush_service.py` | Periodic background export of live sensor history for crash recovery | no |
| `services/sensor_coordinator_service.py` | Central plugin lifecycle/status wrapper with manifest, backpressure, and timing diagnostics | careful |
| `services/clock_sync_service.py` | Bounded tablet/worker offset and RTT histories for timing diagnostics | careful |
| `services/recovery_service.py` | Finds crash-orphaned sessions and finalizes or discards them | no |
| `services/nextcloud_service.py` | Uploads session files to writable Nextcloud public shares over WebDAV | careful |
| `services/update_service.py` | In-app updater: manifest fetch, signature check, download, staging | no |
| `services/ssl_service.py` | Local HTTPS certificate authority for tablet camera access | no |
| `services/certificate_download_service.py` | Plain-HTTP, one-file bootstrap download for the local root CA | careful |
| `services/certificate_transfer_service.py` | Validates, exports, and transactionally imports the reusable local root CA | no |
| `services/branding_service.py` | Validates logo uploads and resolves a slot to a stored file, never to a caller's path | no |
| `services/upload_jobs_service.py` | Persistent upload journal, crash replay, backoff worker, and retry state | no |
| `services/upload_runtime.py` | Registers manifest-declared destination plugin handlers with persistent upload jobs | no |
| `services/folder_open_service.py` | Validates and opens result folders on Windows or macOS | no |
| `services/runtime_config.py` | Paths, ports, app mode, data-folder resolution | careful |
| `services/study_config_service.py` | Load/save the active study and the saved-studies folder | careful |
| `services/study_run_state_service.py` | Persists the operator-controlled loaded/running/completed run state | careful |
| `services/study_sensor_runtime.py` | Which sensors are effectively on (study settings + overrides) | careful |
| `services/trial_service.py` | Sends stimulus start/stop markers to the integrations | careful |
| `services/study_client_service.py` | Tablet heartbeat bookkeeping | careful |
| `services/secrets_service.py` | Keeps the Notion API key backend-local | no |
| `services/study_secrets_service.py` | Per-study credential overrides, never written into the exported study | no |
| `services/study_readiness_service.py` | Pre-run check: what would stop the loaded study from delivering results | careful |
| `services/plugin_settings_service.py` | Manifest-driven machine settings: schema, validation, targeted deep-merge writes | no |
| `services/hardware_settings_service.py` | Saves hardware_settings.json | careful |
| `services/shortcut_service.py` | Creates the desktop shortcut | careful |
| `services/admin_status_service.py` | Aggregates integration status for the dashboard | careful |
| `services/artifact_manifest_service.py` | Owns artifact provenance, checksums, completion markers, and guarded source purge | no |
| `services/card_summary_service.py` | Pure merged-XDF-to-card-statistics derivation | no |
| `services/finalization_runtime.py` | Wires the persistent finalizer to recording and upload adapters | no |
| `services/finalization_service.py` | Durable, idempotent session-finalization state machine and journal replay | no |
| `services/destination_plugin_service.py` | Converts upload-destination manifests into persisted finalization steps and recovery/purge policies | no |
| `services/plugin_health_poll_service.py` | Manifest-paced, non-blocking per-plugin health cache and bounded poll executor | careful |
| `services/recording_runtime.py` | Public compatibility facade plus session-level recording orchestration; contains no process-launch or scientific-validation implementation | no |
| `services/recording_dependencies.py` | liblsl dependency probe and capability-based selection of study/internal recording providers | no |
| `services/recording_worker_launcher.py` | Detached Python/self worker process command, isolation flags, and startup health handshake | no |
| `services/recording_runtime_support.py` | Shared recording error, constants, safe session-path/JSON helpers, recovery grid, and required-source readiness gate | no |
| `services/recording_quality.py` | Scientific source, backup, gap/drop, lease-expiry, and finalization quality checks | no |
| `services/recording_finalization_adapter.py` | Thin bridge from persistent finalization steps to recording freeze, validation, merge, and shutdown | no |
| `services/study_plugin_config.py` | Migrates legacy sensor/upload/card fields into plugin API v3 settings | careful |
| `services/trial_event_service.py` | Persists idempotent trial events and backend-enforced deadlines | no |

## Backend - recording core (`software/study_runner/backend/recording/`)

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

## Detached recording worker (`software/study_runner/recording_worker/`)

| File | Purpose | Edit? |
|---|---|---|
| `application.py` | Authenticated loopback command server with leases, generation fencing, and durable command replay | no |
| `core.py` | Checked `ctypes` binding for the stable C ABI exposed by `xdf_core` | no |
| `lsl_recording.py` | Threaded LSL inlet capture, validated stream headers, bounded drain, and backup projection | no |
| `runtime.py` | Session lifecycle, segment control, merge orchestration, and worker health state | no |

The audited native XDF writer lives in `software/recording_worker/native/`.
Its CMake target wraps the pinned LabRecorder XDFWriter behind a small C ABI;
it deliberately contains no HTTP, LSL, plugin, or study logic.

## Updater trust chain

| File | Purpose | Edit? |
|---|---|---|
| `software/study_runner/update_crypto.py` | THE shared signed-update wire format + Ed25519 verification | no |
| `software/study_runner/update_keys.py` | Trusted public keys (filled in by CI at release build) | no |
| `software/study_runner/update_helper.py` | Applies a staged update on restart (`--apply-update`) | no |

## Integrations (`software/study_runner/integrations/`)

The folder name, public plugin key, and hardware-config key are deliberately
not assumed to be identical. `test_plugin_registry.py` freezes this compatibility
mapping:

| Folder | Plugin key | Config key |
|---|---|---|
| `brainbit` | `brainbit` | `brainbit` |
| `mr60_mini_radar` | `mini_radar` | `mini_radar` |
| `camera_emotion` | `camera_emotion` | `camera_emotion` |
| `lsl_markers` | `lsl` | `lsl` |
| `osc_touchdesigner` | `osc` | `osc` |
| `notion_upload` | `notion` | `notion` |
| `nextcloud_upload` | `nextcloud` | `nextcloud` |

| File | Purpose | Edit? |
|---|---|---|
| `plugin_api.py` | The IntegrationContext/plugin interface every sensor implements | careful |
| `adapter_utils.py` | Shared timestamps, locked state updates, and config-section lookup | careful |
| `registry.py` | Manifest-driven plugin lookup, generic actions, interval summaries, and sidecar exports | careful |
| `plugin_catalog.py` | Discovers plugin folders and validates API-v3 manifests before import | no |
| `history_buffer.py` | Session-sized ring buffers + gap/truncation detection for all sensors | careful |
| `dependency_utils.py` | Optional auto-install of Python packages sensors need | careful |
| `__init__.py` (all) | Empty package markers | yes |
| `brainbit/adapter.py` | Supervises the BrainBit CLI process (has a TOC docstring) | careful |
| `brainbit/brainbit_realtime_cli.py` | The external BrainBit EEG CLI itself (SOURCE OF TRUTH, mirrored in Sensorik/); also runs inside packaged builds via `--brainbit-cli` | careful |
| `brainbit/plugin.py` | Plugin wrapper: config defaults + lifecycle for BrainBit | careful |
| `mr60_mini_radar/adapter.py` | MR60 heart/breathing radar via serial or BLE, with auto-reconnect | careful |
| `mr60_mini_radar/plugin.py` | Plugin wrapper for the radar | careful |
| `mr60_mini_radar/tools/ble_mr60_receiver.py` | Standalone BLE test receiver for debugging | yes |
| `camera_emotion/adapter.py` | Accepts tablet camera frames and publishes stable LSL streams | careful |
| `camera_emotion/plugin.py` | Single public camera/emotion plugin and generic admin actions | careful |
| `camera_emotion/worker/server.py` | Internal DeepFace worker process (Flask, `--emotion-worker`) | careful |
| `camera_emotion/worker/plugin.py` | Starts, monitors, and repairs the internal worker | careful |
| `camera_emotion/worker/analyzer.py` | Runs DeepFace on one frame | careful |
| `camera_emotion/worker/model_errors.py` | Shared DeepFace error classification + suggested fixes | careful |
| `tablet_camera_emotion/*.py`, `local_emotion_worker/*.py` | One-release import/CLI compatibility shims; never catalog entries | careful |
| `lsl_markers/adapter.py` + `plugin.py` | Publishes study markers as an LSL stream | careful |
| `clock_diagnostics/adapter.py` + `plugin.py` | Hidden LSL wall/client/LSL clock observations at event boundaries | careful |
| `osc_touchdesigner/adapter.py` + `plugin.py` | Forwards values to TouchDesigner via OSC | careful |
| `labrecorder_xdf/plugin.py` | Unregistered legacy collector retained temporarily for compatibility | careful |
| `notion_upload/adapter.py` + `plugin.py` | Uploads result summaries to Notion (with offline queue) | careful |
| `nextcloud_upload/plugin.py` | Declares the hidden Nextcloud destination capability and generic settings schema | careful |

## Web UI (`software/study_runner/web/scripts/`)

| File | Purpose | Edit? |
|---|---|---|
| `study-controller.js` | The participant flow engine: cards, navigation, snapshots, submit, preview mode | careful |
| `admin-controller.js` | Study editor, save/load, QR codes, updates | careful |
| `admin-dashboard-controller.js` | Live sensor dashboard with plain-language statuses | careful |
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
| `lib/study-settings.js` | THE client-side study-settings shape; mirrors `_validate_study_settings` | no |
| `lib/deadline-timer.js` | Monotonic deadline timer whose UI ticks never define elapsed study time | no |
| `lib/finalization-view-model.js` | Pure finalization status/progress view model | careful |
| `lib/timeline-view-model.js` | Pure timeline model: stream grouping, waveform/line classification from the LSL header, zoom window maths | careful |
| `lib/plugin-catalog.js` | Fetches and indexes manifest-derived plugin UI capabilities | careful |
| `lib/participant-plugin-extensions.js` | Failure-isolated lifecycle manager for manifest-declared participant extensions | careful |
| `lib/reliable-event-queue.js` | Session-local idempotent marker/event buffering and retry | no |
| `lib/view-transition.js` | Full-screen sweep between admin views; swaps the view while covered | careful |
| `lib/settings-shell.js` | Shared left-nav/right-panel wiring for both settings surfaces | careful |
| `lib/dom-utils.js` | Shared safe DOM lookup, text/HTML assignment, and escaping helpers | careful |
| `lib/modal.js` | Shared accessible modal lifecycle, modal-shell markup, and the yes/no confirmation | careful |
| `lib/branding.js` | Shared branding fetch and logo rendering for the waiting slide and the hub | careful |
| `lib/ambient-bubbles.js` | Self-contained morphing background for the waiting slide; tune CONFIG at the top | no |
| `lib/settings-page.js` | Shared navigation, setup-step state, and action feedback for settings pages | careful |
| `api-client.js` | Tiny fetch helpers (getJson/postJson) | careful |
| `i18n.js` | Translation loading and the `t()` helper | careful |
| `integrations/camera_emotion/ui/participant.js` | Camera/emotion participant lifecycle extension for preview, stimuli, submit, and heartbeat status | careful |
| `integrations/camera_emotion/ui/camera-capture.js` | Plugin-owned tablet camera capture and frame upload adapter | careful |
| `integrations/brainbit/ui/dashboard.js` | Optional BrainBit rich-status renderer loaded through the manifest extension hook | careful |
| `integrations/mr60_mini_radar/ui/dashboard.js` | Optional MR60 rich-status renderer loaded through the manifest extension hook | careful |
| `integrations/camera_emotion/ui/dashboard.js` | Optional camera/emotion rich-status renderer loaded through the manifest extension hook | careful |
| `study-client-heartbeat.js` | Keeps the tablet visible on the dashboard | careful |
| `qr-code.js` | QR code rendering for the access card | no |
| `cards/index.js` | Registers all card modules | careful |
| `cards/card-slider.js` | Rating scale card (VAS) | careful |
| `cards/card-likert.js` | Likert rating card | careful |
| `cards/card-choice.js` | Single/multiple choice card (chips) | careful |
| `cards/card-semantic.js` | Word-pair (semantic differential) card | careful |
| `cards/card-ranking.js` | Drag-to-order ranking card | careful |
| `cards/card-text.js` | Free-text answer card | careful |
| `cards/card-word-cloud.js` | Word selection card | careful |
| `cards/card-mood-meter.js` | Mood Meter quadrant card | careful |
| `cards/card-multi-slider.js` | Multiple rating scales in one card | careful |
| `cards/card-stimulus.js` | Stimulus card: warmup/active phases, sensor triggers | careful |
| `cards/card-participant-id.js` | Participant identification card + field editor | careful |
| `cards/card-info.js` | The shared editor frame: question text, instruction, note, toggle group | careful |
| `cards/card-finish.js` | The final thank-you card | careful |

Locales (`web/locales/en.json`, `de.json`) hold every UI string; both
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
| `release_tools/release-study-runner.mjs` | The release script: bump, check, tag, push (run via release.ps1) | no |
| `release_tools/verify-release-version.mjs` | CI guard: tag matches version.py | no |

Tests live in `software/tests/` - one file per area, named
`test_<area>.py`. Run everything with:
`python -m unittest discover software/tests`

`software/tests/test_source_install_scripts.py` protects the WinGet/Homebrew,
`.venv`, canonical-core, non-destructive installer, and daily-start contracts.
`software/tests/test_python_constraints.py` protects the exact scientific pins,
platform split, shared installer/CI consumption, and honest non-lockfile scope.
