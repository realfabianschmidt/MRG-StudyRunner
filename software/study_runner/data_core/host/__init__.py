"""DataCore host: runs in the server process.

Getting signal off the hardware and into a file. Session/worker/backup/XDF
validation boundaries, the recording worker's lifecycle, the sensor poll
loop, clock sync, and the quality gates that decide whether a recording is
keepable -- merged here from ``recording/`` and
``backend/services/recording/`` (Phase 4, docs/architecture-1.0-umbau.md),
which were the same responsibility split across two directories only
because the Flask-app-construction cycle that motivated the split no longer
applies once this package itself contains no Flask routes.

The Python process is *not* an XDF writer.  Canonical XDF writing and merging
must be supplied by the bundled native worker through
:mod:`study_runner.data_core.contract.worker_protocol`.  When that worker is
unavailable, the only Python fallback is an explicitly labelled recovery
journal which can never be mistaken for an ``.xdf`` file.

``host`` imports :mod:`study_runner.data_core.contract` freely and must never
import :mod:`study_runner.data_core.worker` (invariant #1).
"""

from .artifacts import ArtifactPaths, ArtifactStore, SessionIdentity

__all__ = [
    "ArtifactPaths",
    "ArtifactStore",
    "SessionIdentity",
]
