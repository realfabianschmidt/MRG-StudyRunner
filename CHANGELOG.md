# Changelog

All notable Study Runner changes are documented here. Release tags use
`app-v<version>` and follow semantic versioning.

## Unreleased

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
