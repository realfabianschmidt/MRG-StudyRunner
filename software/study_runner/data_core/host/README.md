# Recording — the host side

The recording implementation is split across three areas with separate jobs:

| Folder | What it is |
|---|---|
| `study_runner/data_core/host/` | **This one.** The host side: it decides where a session's files live, starts and supervises the worker, sends it commands, and reads the XDF back afterwards. |
| `study_runner/data_core/worker/` | The separate Python process that actually writes XDF. It is launched by this folder and outlives a browser reload. |
| `software/recording_worker/native/` | The C++ XDF core that worker is built on, vendored from App-LabRecorder and pinned by `UPSTREAM_LOCK.json`. |

## What is in here

- `artifacts.py` — the canonical session directory: one folder per session, one
  raw XDF per plugin, and the naming that makes a half-finished run recoverable.
- `coordinator.py` — the conversation with the worker: start a source, allocate a
  segment, merge, stop. Segments are only ever appended to the ledger, never
  overwritten.
- `worker_protocol.py` — the wire format and its command ledger. Every command
  carries an id so a retry replays instead of running twice.
- `worker_binary.py` — finding the worker executable, and saying plainly why it
  is unavailable when it is not there.
- `recovery.py` — the recording lease, so a crashed run is detected rather than
  silently resumed.
- `backup.py` — the labelled slowest-rate projection written alongside the raw
  streams, so a merge failure is not total loss.
- `xdf.py` — reading a finished file back for validation and the session viewer.
- `errors.py` — the failure types the rest of the app matches on.

## Why it is separate from the HTTP server

Nothing here imports Flask, and the worker keeps running when no request is in
flight. `apps/server/` translates HTTP requests; `runtime_core/` coordinates a
study; this package owns recording state, artifacts, and worker control.
