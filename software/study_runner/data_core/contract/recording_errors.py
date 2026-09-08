"""Typed errors at the recording/worker boundary.

Moved here from `recording.errors` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.5): both `recording/` (host) and
`recording_worker/` (worker) raise and catch these, and the two may not
import each other (invariant #1). `recording.errors` re-exports this module
so existing callers on both sides keep working unchanged.
"""
from __future__ import annotations


class RecordingError(RuntimeError):
    """Base class for durable recording failures."""


class WorkerProtocolError(RecordingError):
    """A worker command or response violates the local protocol."""


class WorkerUnavailableError(RecordingError):
    """The bundled recording worker is not reachable or not installed."""


class CommandConflictError(WorkerProtocolError):
    """A command id was reused for a different command payload."""


class CommandInProgressError(WorkerProtocolError):
    """A prior command stopped in an indeterminate, in-progress state."""


class XdfBackendUnavailableError(RecordingError):
    """Canonical XDF writing or merging has no available native backend."""
