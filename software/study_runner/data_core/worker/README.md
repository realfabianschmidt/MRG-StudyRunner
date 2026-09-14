# DataCore — the worker side

The separate, detached process that actually writes XDF. It outlives a
browser reload and even a Flask crash: the host starts it once per session
and talks to it only through the authenticated command protocol in
`data_core/contract/worker_protocol.py`. This package may freely import
`data_core/contract/` but must never import `data_core/host/`. The two
processes communicate through the shared command protocol.

## Files

| File | What it does | In / Out |
|---|---|---|
| `application.py` | The process entry point: a tiny loopback-only HTTP server (stdlib only, no Flask) that accepts one endpoint, `/v1/commands`, rejects anything not from `127.0.0.1`, and hands each request to the authenticated command router. Runs until told to shut down or replaced by a newer worker generation. | CLI args (`--state-file`, `--session-dir`, `--xdf-core`) → a running worker process |
| `runtime.py` | `RecordingWorkerRuntime` — the actual command handlers behind that HTTP endpoint: start a recording source, start the backup projection, freeze, merge, shut down, plus the health/attention reporting an operator sees if something goes wrong. This is where a command from the host turns into real work. | a `WorkerCommand` → its result, plus a recovery/attention journal on disk |
| `lsl_recording.py` | Where samples actually come from: pulls chunks off one plugin's LSL stream and writes them with the native core, judges each stream's quality live (via `contracts/quality_journal.py`), and writes periodic durability checkpoints. `BackupRecorder` does the same for the slow safety-copy stream from `data_core/contract/backup_projection.py`. The biggest file here. | an LSL inlet's samples → XDF stream data + quality/timing journal lines |
| `core.py` | The `ctypes` boundary to the native XDF core library itself: opens a writer, converts Python values to the exact C types the library expects, and turns a non-zero native status code into a Python exception with the library's own error text. | Python values → native XDF writer calls |
| `session_journals.py` | One shared file-handle manager for a session's `quality.jsonl` and `timing.jsonl` (and, durably, its `checkpoints.jsonl`) — every recorder in the process writes through this one writer rather than opening the files itself. | a journal record → an appended, periodically-flushed line on disk |
| `__init__.py` | Re-exports `CoreProbe`/`NativeXdfCore`/`NativeXdfError`/`probe_core_library` as this package's small public surface. | — |
