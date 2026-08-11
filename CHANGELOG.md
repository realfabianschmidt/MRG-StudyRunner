# Changelog

All notable Study Runner changes are documented here. Release tags use
`app-v<version>` and follow semantic versioning.

## Unreleased

## 0.7.0 - 2026-08-11

### Changed

- The dev server's terminal output is quiet by default: only failed requests
  (4xx/5xx) print an access-log line, instead of every single request. This
  keeps the startup banner (admin URL, data folder, certificate paths) and
  the app's own rare status lines (`[CONFIG] Saved.`, plugin
  restarts/failures) readable instead of buried under the admin dashboard's
  routine status polling. Set `STUDY_RUNNER_DEBUG=1` for the full per-request
  access log.

## 0.6.0 - 2026-08-11

### Added

- Plugin API v4: every built-in plugin now runs as a supervised subprocess
  behind a single `driver.py` entry point, talking to the core over a
  line-oriented stdio protocol. The core no longer imports any plugin's
  Python module directly.
- A generic admin diagnostics console for every plugin: a guided status view
  plus a line-oriented expert console, with bounded automatic restarts, log
  rotation, and a read-only-during-study stdin gate that requires an
  explicit, recorded local unlock.
- BrainBit: startup-time validation of the pinned EmotionalMath SDK surface
  (fails closed before any device scan instead of only on the first EEG
  batch), queue-overflow protection with a counted drop metric, a measured
  (not nominal) sample-rate field on the live EEG stream, and a faster
  status refresh.
- An explicit, confirmation-gated endpoint to erase a removed plugin's
  leftover machine-config and secret sections, once an operator is sure the
  plugin is gone for good.
- A README for every built-in plugin describing its architecture and, where
  applicable, exactly which parts of its code come from an official vendor
  SDK (BrainBit NeuroSDK/EmotionalMath, Seeed's MR60BHA2 Arduino library,
  DeepFace, the Notion and OSC SDKs) versus project-original code.

### Changed

- Trial timing and persistence hardened: durable prepare-before-stimulus
  journaling with an armed emergency stop, fail-closed admin overrides that
  persist before releasing a card, idempotent stop handling, and a shared
  atomic writer with revision checks for study, hardware, and secret files.
- Every built-in plugin folder is fully removable without breaking the app,
  admin page, hardware save, or build. A `recording-plan.json` contract
  snapshot pins each session's manifests, streams, and backup projections
  for recovery and finalization.

### Fixed

- Hardware-config saves triggered from a plugin's background thread no
  longer fail with a false "changed concurrently" conflict when a section
  had simply never been persisted before.
- Study readiness no longer reports "not ready" for a missing *optional*
  plugin, which is informational, not a misconfiguration; it still blocks
  correctly when the plugin is required.
- Two admin-action tests (BrainBit device selection, Nextcloud connection
  test) were mocking server-process state while the real work runs inside
  each plugin's supervised child process; both now observe the actual
  process boundary. A related test-isolation bug that could leave a stale
  plugin-runtime singleton behind between test files is also fixed.

### Security

- The shipped `hardware_settings.json` template had accidentally picked up
  one real BrainBit headset's MAC address and serial number; reset to empty
  placeholders, and a test now guards against that happening a third time.
  Curated example studies and one demo result are tracked deliberately;
  everything else under `study_content/studies/` and `saved_results/` stays
  gitignored.
- Third-party license texts are now collected in one place (`licenses/`) in
  addition to living next to the vendored code each one covers.

## 0.5.0 - 2026-08-06

### Added

- Plugin API v3 with trusted directory discovery, manifest validation,
  capability-driven settings/actions/readiness, and failure isolation.
- A detached Python recording worker and a small audited native XDF core based
  on the pinned App-LabRecorder/XDFWriter v1.17.1 source.
- Per-plugin segmented raw XDF recording, a labelled slowest-rate backup XDF,
  bounded-memory lossless merge, raw PyXDF validation, and merge-parity checks.
- Crash-safe canonical session directories and a persistent, retryable
  finalization state machine for XDF validation, card summaries, Notion,
  Nextcloud, and guarded local-source cleanup.
- Deadline-based participant timing, durable marker/card event replay, tab
  visibility quality metadata, worker leases, and recording recovery.
- Generic finalization progress, warnings, retries, degraded confirmation, and
  artifact inspection in the Admin UI.
- Source-first Windows and macOS installation/start scripts and verified GitHub
  source-release artifacts with checksums and build metadata.

### Changed

- Camera capture and emotion analysis now share the single public plugin key
  `camera_emotion`; previous packages remain compatibility shims only.
- LAN/WLAN acquisition requires native LSL. BLE, serial, browser HTTPS, and
  local adapters publish through a host LSL bridge.
- XDF is recording infrastructure rather than a selectable UI plugin.
- Notion and Nextcloud are manifest-declared upload destinations and remain
  hidden from the sensor dashboard/settings hub.
- Canonical card statistics are derived only from validated merged XDF data;
  RAM summaries are no longer authoritative.
- The interface draws headings in Materiability and body text in Geist. Both
  now ship in source releases, so a source build renders the same as a packaged
  one. Fonts remain forbidden by default and are exempted only per folder that
  documents its terms.
- Releases use the normal Python source-server workflow. Signed/notarized app
  bundles remain a separate future distribution channel.

### Reliability and security

- Submission acknowledgement now follows an atomic local commit and is
  idempotent by `submission_id`.
- Missing/corrupt required streams, incomplete footers, merge mismatches, and
  statistics failures cannot silently become `completed`.
- Browser sensor ingest requires direct HTTPS plus heartbeat, monotonic
  sequence numbers, and source timestamps.
- Nextcloud uploads immutable artifacts checksum-first and publishes the final
  completion marker last; local raw XDFs are purged only after verified remote
  parity.

## 0.4.0

- Last release before the plugin-based canonical recording architecture.
