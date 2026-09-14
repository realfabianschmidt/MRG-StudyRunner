# Tests

Python tests for the whole backend (`software/study_runner/`), one file per
behaviour area — roughly mirroring the module they test, not a strict 1:1
mapping. 99 files, run with `python -m unittest discover software/tests` (or
`pytest software`).

Three subfolders:

- **`fixtures/`** — small, real example data used by more than one test: a
  legacy flat-result session (`legacy_flat_result/`) and a minimal, genuinely
  working plugin (`packaging_probe/`, with its own `manifest.json`,
  `plugin.py`, and `driver.py`) — real fixtures a test can run against,
  not just parse.
- **`support/`** — shared test-only helper code, never imported by the real
  application: `fixture_plugin.py` (builds a throwaway plugin folder for a
  test), `card_type_fixtures.py` (golden per-card-type config samples),
  `import_graph.py` (the area/import-boundary checker several meta-tests
  share, described further down).
- **`js/`** — frontend tests, run with
  `node --test software/tests/js/*.test.mjs`. Some suites also have thin Python
  wrappers, such as `test_deadline_timer_js.py`, so Python test discovery runs
  them too; those wrappers skip when Node is unavailable. `card-test-support.mjs`
  is a shared helper rather than a standalone test suite. CI and release
  workflows run the Python and JavaScript suites separately, covering the
  JavaScript tests that have no Python wrapper.

## What's tested where

Grouped by the area each test exercises, matching the application folders.

**Server & HTTP routes** (`apps/server/`)
`test_app_server.py`, `test_finalization_routes.py`, `test_recovery_routes.py`,
`test_results_routes.py`, `test_runtime_routes.py`, `test_sessions_routes.py`,
`test_study_credentials_routes.py`, `test_study_session_routes.py`,
`test_trial_timing_routes.py`, `test_update_routes.py`, `test_web_ui.py`
(CDN-free, `/static/`-only, i18n conventions), `test_self_check.py` (the
packaging boot smoke test).

**Frontend** (`apps/ui/`) — the JavaScript tests and Python wrappers, plus
`test_study_settings_contract.py` (the browser and the server must agree on
what a study's settings object looks like).

**Recording** (`data_core/`)
`test_recording_artifacts.py`, `test_recording_backup.py`,
`test_recording_capacity.py`, `test_recording_checkpoint.py`,
`test_recording_core_setup_locator.py`, `test_recording_internal_sources.py`
(the two mandatory non-plugin streams, markers + clock diagnostics),
`test_recording_runtime.py`, `test_recording_worker_protocol.py`,
`test_xdf_recording_contracts.py`, `test_hybrid_recording_worker.py` (a real
native-core run, only when `STUDY_RUNNER_XDF_CORE_TEST` is set),
`test_clock_sync_service.py`, `test_sensor_coordinator_service.py`,
`test_sensor_flush_service.py`, `test_quality_journal.py`,
`test_stream_contract.py`, `test_study_sensor_runtime.py`.

**Plugin framework & cards** (`plugin_framework/`, `contracts/`)
`test_plugin_catalog.py`, `test_plugin_registry.py`,
`test_plugin_process_host.py`, `test_plugin_removability.py` ("a plugin is a
USB stick: pull it out and the software still runs"), `test_plugin_credentials_capability.py`,
`test_plugin_readiness_requirements_capability.py`, `test_no_core_module_names_a_plugin.py`,
`test_fixture_plugin_blueprint.py`, `test_adapter_utils.py`,
`test_history_buffer.py`, `test_card_registry_contract.py`,
`test_card_extension_faults.py`, `test_card_type_fixtures.py`,
`test_study_secrets_service.py` (covers both `settings/secrets_service.py`
and `plugin_framework/plugin_secrets.py` together, since the one story —
per-study credentials — spans both), `test_plugin_sdk.py`
(`tools/plugin_sdk.py`).

**Studies & sessions** (`runtime_core/studies/`)
`test_session_journal.py`, `test_session_lifecycle.py`,
`test_session_quality_summary.py`, `test_session_store.py`,
`test_sessions_browser_contract.py`, `test_study_config_service.py`,
`test_study_plugin_config.py`, `test_study_readiness_service.py`,
`test_trial_event_service.py`, `test_validation.py`,
`test_card_summary_service.py`, `test_results_service.py`,
`test_recovery_service.py`, `test_legacy_flat_result_compat.py`,
`test_real_0_7_0_session_compat.py` (a genuine, unmodified real session, see
below).

**Settings** (`runtime_core/settings/`)
`test_hardware_settings_service.py`, `test_runtime_config.py`,
`test_shortcut_service.py`, `test_certificate_download_service.py`,
`test_certificate_transfer_service.py`, `test_branding.py`,
`test_plugin_settings_service.py`, `test_update_service.py`,
`test_update_installer.py`.

**Delivery** (`runtime_core/delivery/`)
`test_finalization_service.py`, `test_upload_jobs_service.py`,
`test_upload_runtime.py`, `test_withdrawal_service.py`.

**Shared utilities** (`shared/`)
`test_atomic_io.py`, `test_software_root.py`, `test_system_clock_probe.py`.

**Specific built-in plugins** — sensor, destination, and manager tests
`test_brainbit_adapter.py`, `test_brainbit_contract.py`,
`test_brainbit_launch.py`, `test_camera_emotion_worker.py`,
`test_mr60_mini_radar.py`, `test_notion_client_cache.py`,
`test_notion_upload.py`, `test_nextcloud_webdav_client.py`,
`test_study_runner_manager.py` (the historical packaged-build manager tool).

**Architecture & meta** — tests that check the shape of the codebase itself,
not any one module's behaviour
`test_architecture_invariants.py`, `test_area_boundaries.py`,
`test_import_boundaries.py`, `test_import_graph.py`,
`test_measure_structure.py`, `test_file_guide.py` ("keeps
`docs/file-guide.md` honest" — fails the build if the doc drifts from the
real tree), `test_source_install_scripts.py`, `test_python_constraints.py`
(`software/constraints/*.txt`), `conftest.py` (resets the plugin
process-host singletons between tests so one test's driver process can't
leak into the next).

## Characterization tests: pinning exact behaviour, not just "it works"

Most tests check that something produces the *right* result. A handful
instead pin the *exact current* result on purpose, byte for byte or route for
route, because something outside this repository already depends on it not
changing quietly:

- **`test_route_inventory.py`** keeps a hardcoded list of every URL the server
  answers. The test pulls the real list from the running app itself and diffs
  it against that hardcoded copy in both directions — a route that vanished,
  *and* a new route nobody added to the list. The server never reads this
  file; it only exists so a change to the HTTP surface shows up as a visible
  diff in the pull request. It detects accidental or forgotten route changes
  before an installed client encounters them.
- **`test_update_service.py`**'s `test_canonical_asset_payload_bytes_are_frozen`
  does the same for the exact bytes an update manifest is signed over:
  installed clients verify signatures against this exact format, so it must
  never change silently either.
- **`test_real_0_7_0_session_compat.py`** pins that one real, unmodified
  session recorded by the actual 0.7.0 release still opens correctly today.

If one of these ever needs to change on purpose (a real new route, a
deliberate format change), update the pinned expectation in the same commit
that causes it — that is the point where a human is meant to notice and
approve the change, not work around it.
