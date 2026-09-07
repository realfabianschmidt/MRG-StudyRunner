"""Install an optional runtime dependency on demand, or fail with a plain message.

Moved to `shared.dependency_utils` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.4) -- see that module's docstring
for why. Re-exported here so existing plugin callers keep working unchanged.
"""
from __future__ import annotations

from study_runner.shared.dependency_utils import ensure_requirements

__all__ = ["ensure_requirements"]
