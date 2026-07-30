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
| `software/server.py` | Start Study Runner from source (`python server.py`); dispatches the `--emotion-worker`, `--brainbit-cli` and updater CLI flags | careful |
| `software/study_runner/app_server.py` | Wires up the Flask server: port check, HTTPS, startup banner, browser open | careful |
| `software/study_runner/version.py` | The single version number of the app | yes |
| `tools/study_runner_manager.py` | Standalone Install & Repair Wizard (downloads, verifies, installs releases) | no |

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
| `routes/uploads.py` | Background-upload status/retry and validated result-folder opening | no |
| `routes/helpers.py` | Shared request helpers: runtime config, sessions, sensor runtime | careful |

## Backend - services (`software/study_runner/backend/services/`)

| File | Purpose | Edit? |
|---|---|---|
| `services/__init__.py` | Empty package marker | yes |
| `services/atomic_io.py` | Crash-safe JSON writes (temp file + replace) for all study data | no |
| `services/validation.py` | Validates study configs and submitted results (has a TOC docstring) | careful |
| `services/results_service.py` | Builds answer details, slices biosignals per card, writes result files | no |
| `services/sessions_index_service.py` | Scans completed results and builds bounded timeline envelopes | careful |
| `services/nextcloud_service.py` | Uploads session files to writable Nextcloud public shares over WebDAV | careful |
| `services/update_service.py` | In-app updater: manifest fetch, signature check, download, staging | no |
| `services/ssl_service.py` | Local HTTPS certificate authority for tablet camera access | no |
| `services/certificate_download_service.py` | Plain-HTTP, one-file bootstrap download for the local root CA | careful |
| `services/certificate_transfer_service.py` | Validates, exports, and transactionally imports the reusable local root CA | no |
| `services/upload_jobs_service.py` | Persistent upload journal, crash replay, backoff worker, and retry state | no |
| `services/upload_runtime.py` | Connects upload jobs to Flask plus the Notion and Nextcloud executors | no |
| `services/folder_open_service.py` | Validates and opens result folders on Windows or macOS | no |
| `services/runtime_config.py` | Paths, ports, app mode, data-folder resolution | careful |
| `services/study_config_service.py` | Load/save the active study and the saved-studies folder | careful |
| `services/study_sensor_runtime.py` | Which sensors are effectively on (study settings + overrides) | careful |
| `services/trial_service.py` | Sends stimulus start/stop markers to the integrations | careful |
| `services/study_client_service.py` | Tablet heartbeat bookkeeping | careful |
| `services/secrets_service.py` | Keeps the Notion API key backend-local | no |
| `services/hardware_settings_service.py` | Saves hardware_settings.json | careful |
| `services/shortcut_service.py` | Creates the desktop shortcut | careful |
| `services/admin_status_service.py` | Aggregates integration status for the dashboard | careful |

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
| `tablet_camera_emotion` | `camera_emotion` | `camera_emotion` |
| `local_emotion_worker` | `emotion_worker` | `camera_emotion` |
| `lsl_markers` | `lsl` | `lsl` |
| `osc_touchdesigner` | `osc` | `osc` |
| `labrecorder_xdf` | `labrecorder` | `labrecorder` |
| `notion_upload` | `notion` | `notion` |

| File | Purpose | Edit? |
|---|---|---|
| `plugin_api.py` | The IntegrationContext/plugin interface every sensor implements | careful |
| `adapter_utils.py` | Shared timestamps, locked state updates, and config-section lookup | careful |
| `registry.py` | Lists all plugins; builds interval summaries and sidecar exports | careful |
| `history_buffer.py` | Session-sized ring buffers + gap/truncation detection for all sensors | careful |
| `dependency_utils.py` | Optional auto-install of Python packages sensors need | careful |
| `__init__.py` (all) | Empty package markers | yes |
| `brainbit/adapter.py` | Supervises the BrainBit CLI process (has a TOC docstring) | careful |
| `brainbit/brainbit_realtime_cli.py` | The external BrainBit EEG CLI itself (SOURCE OF TRUTH, mirrored in Sensorik/); also runs inside packaged builds via `--brainbit-cli` | careful |
| `brainbit/plugin.py` | Plugin wrapper: config defaults + lifecycle for BrainBit | careful |
| `mr60_mini_radar/adapter.py` | MR60 heart/breathing radar via serial or BLE, with auto-reconnect | careful |
| `mr60_mini_radar/plugin.py` | Plugin wrapper for the radar | careful |
| `mr60_mini_radar/tools/ble_mr60_receiver.py` | Standalone BLE test receiver for debugging | yes |
| `tablet_camera_emotion/adapter.py` | Accepts tablet camera frames, forwards them for emotion analysis | careful |
| `tablet_camera_emotion/plugin.py` | Plugin wrapper for camera emotion | careful |
| `local_emotion_worker/server.py` | The DeepFace worker process (Flask, `--emotion-worker`) | careful |
| `local_emotion_worker/plugin.py` | Starts/monitors/repairs the worker; auto-restart on crash | careful |
| `local_emotion_worker/analyzer.py` | Runs DeepFace on one frame | careful |
| `local_emotion_worker/model_errors.py` | Shared DeepFace error classification + suggested fixes | careful |
| `lsl_markers/adapter.py` + `plugin.py` | Publishes study markers as an LSL stream | careful |
| `osc_touchdesigner/adapter.py` + `plugin.py` | Forwards values to TouchDesigner via OSC | careful |
| `labrecorder_xdf/plugin.py` | Collects the LabRecorder .xdf file into the result folder | careful |
| `notion_upload/adapter.py` + `plugin.py` | Uploads result summaries to Notion (with offline queue) | careful |

## Web UI (`software/study_runner/web/scripts/`)

| File | Purpose | Edit? |
|---|---|---|
| `study-controller.js` | The participant flow engine: cards, navigation, snapshots, submit | careful |
| `admin-controller.js` | Study editor, save/load, QR codes, updates | careful |
| `admin-dashboard-controller.js` | Live sensor dashboard with plain-language statuses | careful |
| `notion-settings-controller.js` | The Notion settings page | careful |
| `admin/nextcloud-settings-controller.js` | The shared-shell Nextcloud setup and connection-test page | careful |
| `admin/certificate-settings-controller.js` | The shared-shell certificate status, setup, export, and import page | no |
| `admin/session-timeline.js` | Renders completed-session sensor lanes and answer markers as offline SVG | careful |
| `lib/dom-utils.js` | Shared safe DOM lookup, text/HTML assignment, and escaping helpers | careful |
| `lib/modal.js` | Shared accessible modal lifecycle and existing modal-shell markup | careful |
| `lib/settings-page.js` | Shared navigation, setup-step state, and action feedback for settings pages | careful |
| `api-client.js` | Tiny fetch helpers (getJson/postJson) | careful |
| `i18n.js` | Translation loading and the `t()` helper | careful |
| `camera-capture.js` | Captures tablet camera frames and posts them to the server | careful |
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
| `cards/card-info.js` | Shared instruction rendering used by other cards | careful |
| `cards/card-finish.js` | The final thank-you card | careful |

Locales (`web/locales/en.json`, `de.json`) hold every UI string; both
files must have identical keys (a test checks this). `web/vendor/`
holds offline copies of third-party assets (icons).

## Release tooling (`release_tools/`, repo root)

| File | Purpose | Edit? |
|---|---|---|
| `release_tools/build_python_onedir.py` | Runs PyInstaller for server/manager | careful |
| `release_tools/package_python_onedir.py` | Zips a onedir build into a release asset | careful |
| `release_tools/build_python_update_manifest.py` | Signs release assets into the update manifest | no |
| `release_tools/write_python_update_key.py` | Bakes the trusted public key into CI builds | no |
| `release_tools/fetch_deepface_model_assets.py` | Downloads the DeepFace weights into model_assets/ | careful |
| `release_tools/build_offline_wheelhouse.py` | Builds an offline pip wheelhouse | careful |
| `release_tools/pyinstaller/study_runner_server_common.py` | Shared PyInstaller spec pieces (datas, hidden imports) | careful |
| `release_tools/release-study-runner.mjs` | The release script: bump, check, tag, push (run via release.ps1) | no |
| `release_tools/verify-release-version.mjs` | CI guard: tag matches version.py | no |

Tests live in `software/tests/` - one file per area, named
`test_<area>.py`. Run everything with:
`python -m unittest discover software/tests`
