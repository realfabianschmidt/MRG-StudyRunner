"""Typed errors at the recording/worker boundary."""


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
