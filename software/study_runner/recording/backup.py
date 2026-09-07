"""Slowest-grid backup projection scheduling.

Moved to `shared.backup_projection` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.5) -- see that module's docstring
for why. Re-exported here so existing host-side callers keep working
unchanged.
"""
from __future__ import annotations

from study_runner.shared.backup_projection import (
    STATUS_DEGRADED,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_SOURCE_DEGRADED,
    STATUS_STALE,
    STATUS_VALID,
    BackupChannel,
    BackupFrame,
    BackupProjection,
    BackupSampler,
    CachedProjection,
    choose_backup_rate,
    projections_from_manifest,
)

__all__ = [
    "STATUS_DEGRADED",
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_SOURCE_DEGRADED",
    "STATUS_STALE",
    "STATUS_VALID",
    "BackupChannel",
    "BackupFrame",
    "BackupProjection",
    "BackupSampler",
    "CachedProjection",
    "choose_backup_rate",
    "projections_from_manifest",
]
