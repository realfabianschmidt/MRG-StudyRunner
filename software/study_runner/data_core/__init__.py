"""DataCore: LSL ingest, XDF recording, timing, QC, merge and recovery.

Split by which process runs the code, not by topic:

- ``contract``  wire types and pure probes both processes import: the
                worker protocol, typed errors, backup-projection model, the
                recording lease, the native-core probe, LSL dependency
                checks. Imports nothing else in the application.
- ``host``      runs in the server process: coordination, launching,
                recovery, XDF-merge validation and the recording contract.
- ``worker``    runs in the detached worker process: LSL ingest, command
                handling, the ctypes binding to the native XDF core.

``host`` and ``worker`` both import ``contract`` and never each other
(invariant #1, docs/archive/architecture-1.0-umbau.md).
"""
