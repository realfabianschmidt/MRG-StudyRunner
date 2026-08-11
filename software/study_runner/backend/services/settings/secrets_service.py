from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, TypeVar

from study_runner.shared.atomic_io import atomic_path_lock, atomic_write_json

from ..studies.study_secrets_service import describe_secret_state, secret_fields


class LocalSecretsError(RuntimeError):
    """The credential store exists but cannot be trusted."""


UpdateResult = TypeVar("UpdateResult")


def load_local_secrets(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalSecretsError(f"Could not read local secrets from {path}: {error}") from error
    if not isinstance(payload, dict):
        raise LocalSecretsError(f"Local secrets in {path} must be a JSON object.")
    return payload


def save_local_secrets(path: Path, secrets: dict[str, Any]) -> None:
    atomic_write_json(
        path,
        secrets,
        ensure_ascii=True,
        trailing_newline=True,
    )


def update_local_secrets(
    path: Path,
    updater: Callable[[dict[str, Any]], UpdateResult],
) -> tuple[dict[str, Any], UpdateResult]:
    """Atomically serialize an in-process credential read/modify/write.

    The path lock spans the read as well as the replace. This matters for two
    simultaneous targeted admin updates: locking only the individual writes
    would leave both requests based on the same stale document and silently
    drop whichever credential was saved first.
    """

    with atomic_path_lock(path):
        secrets = load_local_secrets(path)
        result = updater(secrets)
        save_local_secrets(path, secrets)
        return deepcopy(secrets), result


def redact_hardware_config(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str = "",
) -> dict[str, Any]:
    """Strip every secret, keeping only "is one configured, and from where".

    Walks every plugin that declared a `credentials` capability, so a new
    plugin's secret is redacted automatically instead of needing a line added
    here. `*_scope` is added alongside the existing `*_configured` / `*_source`
    keys rather than replacing them, so nothing that already reads this
    payload has to change.
    """
    redacted = deepcopy(hardware_config)
    # A removed plugin no longer has a manifest telling the core which of its
    # fields are credentials.  Never guess and expose that opaque section to a
    # browser or upload job.  It remains untouched in the on-disk source and is
    # represented only by a non-sensitive placeholder until the plugin returns
    # or the operator explicitly removes it.
    from study_runner.plugin_framework.registry import get_plugin_manifests

    installed_config_keys = {
        str(manifest.get("config_key") or plugin_key)
        for plugin_key, manifest in get_plugin_manifests().items()
    }
    core_public_keys = {"_comment", "lsl", "labrecorder"}
    for key in list(redacted):
        if key in installed_config_keys or key in core_public_keys:
            continue
        redacted[key] = {
            "unavailable": True,
            "configured": True,
            "settings_hidden": True,
        }
    for kind, field in secret_fields().items():
        section = redacted.get(kind)
        if not isinstance(section, dict):
            continue
        state = describe_secret_state(kind, hardware_config, local_secrets, study_id)
        section[field] = ""
        section[f"{field}_configured"] = state["configured"]
        section[f"{field}_source"] = state["source"]
        section[f"{field}_scope"] = state["scope"]
    return redacted
