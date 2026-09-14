# Contract (data_core)

The wire formats and pure checks shared by the two processes that record a
study: the Flask host and the detached recording worker. Neither process may
import the other's code directly — that's an architectural rule ("invariant
#1"), not a preference — so anything both genuinely need to agree on lives
here instead, with no dependency beyond the standard library (plus `pylsl`
where a file's whole job is checking for it).

## Files

| File | What it does | In / Out | Used by |
|---|---|---|---|
| `recording_errors.py` | The shared exception types for anything that can go wrong at the host/worker boundary (`WorkerUnavailableError`, `WorkerProtocolError`, and two narrower ones for a replayed command). | — | both sides, wherever a recording failure needs a specific, catchable type |
| `lsl_dependency.py` | Checks that `pylsl`/`liblsl` is actually installed and exposes the functions Study Runner needs, before anything tries to use it, and reports its version for diagnostics. | — → the `pylsl` module, or a clear `RuntimeError` | the host's preflight check, the worker's own startup |
| `native_core_probe.py` | Loads the native XDF core library just far enough to ask it "are you the right ABI, and do you have every feature we require" — without constructing an actual writer. | a `.dll`/`.so` path → a `CoreProbe` (or `NativeXdfError`) | the host's worker-binary locator (before ever starting the worker), and the worker's own writer, which probes itself the same way on construction |
| `recording_lease.py` | A persistent 15-minute lease file: as long as it's refreshed, the worker knows Flask is still alive; once it expires, the worker knows to close its XDF files cleanly on its own rather than wait forever for a host that may never come back. | a session + worker generation → a lease file, refreshed/expired/closed over its lifetime | the worker (holds and refreshes it), the host (starts it, reads it back during recovery) |
| `backup_projection.py` | Schedules the "backup" stream: a slow, fixed-rate safety copy of a handful of numeric channels from the real sensor streams, so even a stream that never got its own working recording still leaves a coarse, honestly-labelled trace (missing/stale/valid/degraded, never a silently-stale number). | manifest `backup_projection` config + live values → fixed-grid `BackupFrame`s | the worker's LSL recording loop |
| `worker_protocol.py` | The authenticated command protocol the host and worker actually speak over loopback HTTP: a bearer token per worker instance, one JSON command/response shape, and a durable ledger so retrying a dropped request replays the same recorded outcome instead of running it twice. The biggest file here. | a command name + payload → a `WorkerResponse`, persisted before it's returned | `data_core/host/*` (the client side) and `data_core/worker/application.py` (the server side) |
| `__init__.py` | One line: what this package is and why it has no dependencies of its own. | — | — |
