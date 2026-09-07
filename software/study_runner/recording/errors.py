"""Typed errors at the recording/worker boundary.

Moved to `shared.recording_errors` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.5) -- see that module's docstring
for why. Re-exported here so existing callers on both sides of the boundary
keep working unchanged.
"""
from __future__ import annotations

from study_runner.shared.recording_errors import (
    CommandConflictError,
    CommandInProgressError,
    RecordingError,
    WorkerProtocolError,
    WorkerUnavailableError,
    XdfBackendUnavailableError,
)

__all__ = [
    "CommandConflictError",
    "CommandInProgressError",
    "RecordingError",
    "WorkerProtocolError",
    "WorkerUnavailableError",
    "XdfBackendUnavailableError",
]
