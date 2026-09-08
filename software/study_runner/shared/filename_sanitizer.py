"""Turn an arbitrary identifier into a safe filename fragment.

Needed by both the results/recovery side (backend/services/studies) and the
live sensor-history flush side (data_core/host) -- neither owns the other,
so it belongs here rather than in either.
"""
from __future__ import annotations

import re

UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_identifier_for_filename(value: str) -> str:
    normalized = UNSAFE_FILENAME_CHARS.sub("_", (value or "study").strip())
    normalized = normalized.strip("._-")
    if not normalized:
        return "study"
    return normalized[:80]
