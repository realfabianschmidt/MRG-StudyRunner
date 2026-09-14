# DataCore — the host side

Runs inside the Flask process. This package gets signal off the hardware and
into a file: it decides where a session's artifacts live, starts and
supervises the separate recording-worker process, sends it commands, polls
every plugin's health, and reads a finished XDF back for validation and the
session viewer.

It is **not** an XDF writer. Canonical XDF writing and merging only ever
happens in the detached worker process (`data_core/worker/`), reached
through `data_core/contract/worker_protocol.py`. `host/` may freely import
`data_core/contract/` but must never import `data_core/worker/`.

## Files

| File | What it does | In / Out |
|---|---|---|
| `artifacts.py` | The canonical on-disk layout for one session: one folder, one raw XDF per plugin, filesystem-safe names, and the SHA-256 hashing used to prove a file wasn't quietly changed. | a session identity → `ArtifactPaths`/`ArtifactStore` |
| `coordinator.py` | The append-only conversation with the worker about *where files live*: allocate a new segment, mark it recording/closing, merge — a ledger that is only ever appended to, never rewritten. | worker commands → `SegmentRecord`s in a `SEGMENT_LEDGER` |
| `worker_binary.py` | Fail-closed discovery of the native XDF core the worker needs: never searches `PATH`, checks a pinned upstream version/commit, and says exactly why it refused rather than guessing. | a build directory → `WorkerBinaryAvailability` |
| `recording_worker_launcher.py` | Actually starts the detached worker process from a token-bearing state file and waits for it to answer. | a `WorkerLaunchSpec` → a running, reachable worker |
| `recording_runtime.py` | The big one: `RecordingRuntimeService` ties everything in this folder together into what `apps/server` actually calls — preflight, start a session, reattach after a crash, refresh the lease, freeze/merge/shut down the worker, inspect the result. | a study + hardware config → a running (or honestly-failed) recording |
| `recording_runtime_support.py` | Small helpers `recording_runtime.py` needs but that don't belong on its own giant class: building a session identity, reserving a free loopback port, parsing timestamps, building the public-facing recording plan. | — |
| `recording_capacity.py` | Preflight storage check: computes required disk space from the declared stream contract (channel count × rate × sample size) and the study's planned duration — never a synthetic disk-speed benchmark, which would always pass and prove nothing. | a recording contract + planned duration → enough free space, or an honest "unknown" |
| `recording_contract.py` | Snapshots the exact plugin manifests, streams, and backup projections a recording started with, hashed so a plugin upgrade or removal mid-session can never quietly change what a recovery or validation check assumes. | selected plugins + manifests → a signed, persisted recording contract |
| `recording_dependencies.py` | Two things: probing that the pinned `pylsl`/native `liblsl` versions are actually what's installed, and deciding which selected plugins actually count as recording sources (including the two mandatory internal ones below). | study settings → probe results / selected-plugin lists |
| `recording_quality.py` | The scientific gate: turns a validation report, a lease state, and a source's health into the specific pass/fail checks finalization uses to decide if a session's data is keepable. | XDF validation + lease + source status → quality check results |
| `xdf.py` | Reads a finished XDF back — through `pyxdf` as a validator only, never as a writer — and checks it against the recording contract: every declared stream present, a lossless merge, consistent metadata. Also defines the (native-worker vs. unavailable) backend boundary and the Python-only fallback recovery journal used when no native worker exists. | XDF files on disk → a `XdfValidationReport` |
| `sensor_coordinator_service.py` | `SensorCoordinator`: starts/stops/polls plugins on the operator's behalf, with a stale-while-revalidate cache so a slow or hung plugin handler never blocks an admin HTTP request. | a plugin key + action → the plugin's result, cached status reads |
| `plugin_health_poll_service.py` | The polling engine `sensor_coordinator_service.py` is built on: one queued-or-running poll per plugin at a time, on a bounded thread pool, paced by each plugin's own manifest interval. | a plugin's status handler → a cached, periodically-refreshed status |
| `sensor_flush_service.py` | A background timer that re-exports every active session's live sensor history to disk every interval, so a crash never loses more than one interval's worth of biosignal data (answers are already saved per-card; this is what does the same for raw sensor readings). | live in-memory sensor history → periodic flush files on disk |
| `study_sensor_runtime.py` | Works out which sensors are actually "on" right now from three layers — hardware config, the study's own selection, and a temporary session override — into one `effective` answer. | hardware config + study settings + overrides → the effective sensor on/off state |
| `clock_sync_service.py` | Keeps a bounded recent history of clock-offset/round-trip-time samples per tablet or worker, so the admin dashboard can show how well-synced each clock actually is. | timestamped exchanges → per-source offset/RTT history |
| `markers.py` | The study's own event-marker LSL stream — always present, not a plugin (it fails the test that defines what a plugin is: nothing can run without it). Loads its manifest through the same validator real plugins use, purely to keep one declared shape instead of two hand-kept copies. | a trial/card event → one LSL marker sample |
| `clock_diagnostics.py` | The second mandatory internal stream: ties the browser's wall clock, its own reported offset, and the LSL clock together at every trial/marker boundary, for after-the-fact timing analysis. | a trial/marker event's timing fields → one LSL diagnostics sample |
| `__init__.py` | What this package is, the writer/prober distinction, and the invariant that it may use `data_core/contract/` but never `data_core/worker/`. Re-exports `ArtifactPaths`/`ArtifactStore`/`SessionIdentity` as this package's small public surface. | — |
