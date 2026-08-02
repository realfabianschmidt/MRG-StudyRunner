"""Crash-safe recording primitives for the Study Runner.

This package deliberately contains no Flask routes.  It defines the durable
session, worker, backup and XDF validation boundaries used by the recording
coordinator and by the finalization state machine.

The Python process is *not* an XDF writer.  Canonical XDF writing and merging
must be supplied by the bundled native worker through :mod:`worker_protocol`.
When that worker is unavailable, the only Python fallback is an explicitly
labelled recovery journal which can never be mistaken for an ``.xdf`` file.
"""

from .artifacts import ArtifactPaths, ArtifactStore, SessionIdentity
from .backup import (
    BackupChannel,
    BackupFrame,
    BackupProjection,
    BackupSampler,
    choose_backup_rate,
    projections_from_manifest,
)
from .errors import (
    CommandConflictError,
    CommandInProgressError,
    RecordingError,
    WorkerProtocolError,
    WorkerUnavailableError,
    XdfBackendUnavailableError,
)

__all__ = [
    "ArtifactPaths",
    "ArtifactStore",
    "BackupChannel",
    "BackupFrame",
    "BackupProjection",
    "BackupSampler",
    "CommandConflictError",
    "CommandInProgressError",
    "RecordingError",
    "SessionIdentity",
    "WorkerProtocolError",
    "WorkerUnavailableError",
    "XdfBackendUnavailableError",
    "choose_backup_rate",
    "projections_from_manifest",
]
