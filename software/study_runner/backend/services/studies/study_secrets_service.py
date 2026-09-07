"""Per-study credentials, kept out of the study file.

Moved to `plugin_framework.plugin_secrets` during the 1.0 rebuild
(docs/architecture-1.0-umbau.md, Phase 2.2) -- see that module's docstring
for why (nothing here ever needed Flask; the resolver has to run inside a
plugin's own subprocess, which may not import `backend`). Re-exported here so
existing callers (routes, `study_readiness_service.py`, the admin credential
routes) keep working unchanged.
"""
from __future__ import annotations

from study_runner.plugin_framework.plugin_secrets import (
    _credential_declarations,
    copy_study_secrets,
    describe_secret_state,
    describe_secret_storage_location,
    forget_study_secrets,
    get_study_secret,
    list_study_credential_state,
    resolve_plugin_secret,
    secret_fields,
    set_study_secret,
    study_key,
)

__all__ = [
    "copy_study_secrets",
    "describe_secret_state",
    "describe_secret_storage_location",
    "forget_study_secrets",
    "get_study_secret",
    "list_study_credential_state",
    "resolve_plugin_secret",
    "secret_fields",
    "set_study_secret",
    "study_key",
]
