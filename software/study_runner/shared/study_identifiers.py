"""The one place a study's stable id gets normalized.

Moved out of `backend.services.studies.study_config_service` during the 1.0
rebuild (docs/architecture-1.0-umbau.md, Phase 2.2): `plugin_framework`'s
credential resolution needs the exact same normalization a study's filename
and its credential-storage key already use, so a rename can never strand
secrets under the old key. Putting it in `shared/` lets both areas use the
one function instead of each keeping its own copy that could drift.
"""
from __future__ import annotations


def normalize_study_id(study_id: str) -> str:
    """The study's stable key: its filename stem, and its credential key."""
    return "".join(c for c in study_id if c.isalnum() or c in " _-") or "unnamed"
