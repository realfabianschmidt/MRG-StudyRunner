"""The canonical order of built-in participant identification fields.

Moved out of `backend.services.studies.validation` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.3): the Notion destination plugin
needs this same order to build its participant-metadata schema and property
list, and a plugin may not import `backend`. It is a plain data constant with
no dependency of its own, so there was nothing else to carry along.

`validation.py` re-exports this so its existing callers keep working
unchanged.
"""
from __future__ import annotations

PARTICIPANT_FIELD_ORDER = [
    "first_name",
    "last_name",
    "age_group",
    "gender",
    "childhood_area",
    "childhood_nearest_city",
    "birth_place",
    "birth_date",
]
