"""Authenticated, idempotent loopback protocol for the bundled XDF worker.

Moved to `shared.worker_protocol` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.5) -- see that module's docstring
for why. Re-exported here so existing callers on both sides of the boundary
keep working unchanged.
"""
from __future__ import annotations

from study_runner.shared.worker_protocol import (
    COMMAND_LEDGER_SCHEMA,
    DEFAULT_WORKER_HOST,
    WORKER_PROTOCOL_VERSION,
    WORKER_STATE_SCHEMA,
    LoopbackWorkerClient,
    PersistentCommandLedger,
    WorkerCommand,
    WorkerCommandRouter,
    WorkerEndpointState,
    WorkerResponse,
    WorkerStateStore,
    WorkerTransport,
)

__all__ = [
    "COMMAND_LEDGER_SCHEMA",
    "DEFAULT_WORKER_HOST",
    "WORKER_PROTOCOL_VERSION",
    "WORKER_STATE_SCHEMA",
    "LoopbackWorkerClient",
    "PersistentCommandLedger",
    "WorkerCommand",
    "WorkerCommandRouter",
    "WorkerEndpointState",
    "WorkerResponse",
    "WorkerStateStore",
    "WorkerTransport",
]
