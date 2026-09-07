"""Persistent 15-minute recording lease used after web-server loss.

Moved to `shared.recording_lease` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.5) -- see that module's docstring
for why. Re-exported here so existing host-side callers keep working
unchanged.
"""
from __future__ import annotations

from study_runner.shared.recording_lease import (
    DEFAULT_RECORDING_LEASE_SECONDS,
    LEASE_SCHEMA,
    RecordingLease,
    RecordingLeaseStore,
)

__all__ = [
    "DEFAULT_RECORDING_LEASE_SECONDS",
    "LEASE_SCHEMA",
    "RecordingLease",
    "RecordingLeaseStore",
]
