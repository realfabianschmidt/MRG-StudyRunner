"""Per-study credentials, kept out of the study file.

A study carries its upload *targets* (Nextcloud share link, Notion page and
database) so it runs on another computer. It must never carry the *credentials*
for them: `study_settings` is serialized verbatim into the exported
`.study-runner` file, so an API key placed there would travel to whoever
receives the study.

Credentials therefore live beside `hardware_settings.json` in
`local_secrets.json`, under a `studies` subtree keyed by the study's normalized
id - the same normalizer that picks its filename, so the two can never drift:

    {
      "notion":    {"api_key": "..."},          # machine-wide fallback
      "nextcloud": {"password": "..."},         # machine-wide fallback
      "studies": {
        "<normalized id>": {
          "notion":    {"api_key": "..."},
          "nextcloud": {"password": "..."}
        }
      }
    }

Per-study entries are overrides, not replacements: a lab with one Notion
workspace configures the machine key once and only overrides the one study that
needs a different account. That also means a study imported from another
computer falls back to this computer's key instead of failing.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .study_config_service import normalize_study_id


def _credential_declarations() -> dict[str, dict[str, Any]]:
    """plugin_key -> its one declared `credentials` capability, if any.

    Derived from the catalog rather than a hardcoded map, so a plugin that
    needs a secret declares it in its own manifest and nothing else has to
    know the field or environment variable name.
    """
    from study_runner.plugin_framework.registry import get_plugin_manifests

    declarations: dict[str, dict[str, Any]] = {}
    for plugin_key, manifest in get_plugin_manifests().items():
        credentials = (manifest.get("capability_config") or {}).get("credentials")
        if isinstance(credentials, dict) and credentials.get("config_field"):
            declarations[plugin_key] = credentials
    return declarations


def secret_fields() -> dict[str, str]:
    """Kinds of secret a study can override, and where each lives in a section."""
    return {kind: str(decl["config_field"]) for kind, decl in _credential_declarations().items()}


def study_key(study_id: str) -> str:
    """The credential key for a study. Empty ids get no entry, never a shared one."""
    normalized = normalize_study_id(str(study_id or "").strip())
    return "" if normalized == "unnamed" and not str(study_id or "").strip() else normalized


def get_study_secret(local_secrets: dict[str, Any], study_id: str, kind: str) -> str:
    """This study's own secret, or "" when it has none."""
    field = secret_fields().get(kind)
    key = study_key(study_id)
    if not field or not key:
        return ""
    studies = local_secrets.get("studies")
    if not isinstance(studies, dict):
        return ""
    entry = studies.get(key)
    if not isinstance(entry, dict):
        return ""
    section = entry.get(kind)
    if not isinstance(section, dict):
        return ""
    value = section.get(field)
    return value.strip() if isinstance(value, str) else ""


def set_study_secret(local_secrets: dict[str, Any], study_id: str, kind: str, value: str) -> dict[str, Any]:
    """Store or clear one secret for one study. Returns the updated secrets."""
    field = secret_fields().get(kind)
    key = study_key(study_id)
    if not field:
        raise ValueError(f"Unknown credential kind: {kind}")
    if not key:
        raise ValueError("A study id is required to store a credential.")

    studies = local_secrets.setdefault("studies", {})
    if not isinstance(studies, dict):
        studies = {}
        local_secrets["studies"] = studies

    entry = studies.setdefault(key, {})
    if not isinstance(entry, dict):
        entry = {}
        studies[key] = entry

    section = entry.setdefault(kind, {})
    if not isinstance(section, dict):
        section = {}
        entry[kind] = section

    normalized = str(value or "").strip()
    if normalized:
        section[field] = normalized
    else:
        section.pop(field, None)

    # Prune empties so a cleared credential leaves no misleading shell behind.
    if not section:
        entry.pop(kind, None)
    if not entry:
        studies.pop(key, None)
    if not studies:
        local_secrets.pop("studies", None)
    return local_secrets


def copy_study_secrets(local_secrets: dict[str, Any], from_study_id: str, to_study_id: str) -> bool:
    """Carry credentials to a renamed study.

    Copy, not move: `save_study` never deletes the old file, so a rename leaves
    a second study behind that would otherwise lose its credentials.
    """
    source_key = study_key(from_study_id)
    target_key = study_key(to_study_id)
    if not source_key or not target_key or source_key == target_key:
        return False

    studies = local_secrets.get("studies")
    if not isinstance(studies, dict):
        return False
    entry = studies.get(source_key)
    if not isinstance(entry, dict) or not entry:
        return False

    studies[target_key] = {
        kind: dict(section)
        for kind, section in entry.items()
        if isinstance(section, dict)
    }
    return True


def forget_study_secrets(local_secrets: dict[str, Any], study_id: str) -> bool:
    """Drop a deleted study's credentials instead of leaving them on disk."""
    key = study_key(study_id)
    studies = local_secrets.get("studies")
    if not key or not isinstance(studies, dict) or key not in studies:
        return False
    studies.pop(key)
    if not studies:
        local_secrets.pop("studies", None)
    return True


def describe_secret_state(
    kind: str,
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str = "",
) -> dict[str, Any]:
    """Where the secret used for this study actually comes from.

    `scope` is what the operator needs: "study" means this study has its own,
    "machine" means it is borrowing the shared one, "env" means an environment
    variable overrides both, "none" means nothing is configured.
    """
    declaration = _credential_declarations().get(kind, {})
    field = str(declaration.get("config_field") or "")
    env_var = str(declaration.get("env_var") or "")
    if env_var and os.getenv(env_var, "").strip():
        return {"configured": True, "scope": "env", "source": "env"}

    if get_study_secret(local_secrets, study_id, kind):
        return {"configured": True, "scope": "study", "source": "study_file"}

    section = local_secrets.get(kind)
    machine_value = section.get(field) if isinstance(section, dict) else ""
    if isinstance(machine_value, str) and machine_value.strip():
        return {"configured": True, "scope": "machine", "source": "local_file"}

    legacy_section = hardware_config.get(kind)
    legacy_value = legacy_section.get(field) if isinstance(legacy_section, dict) else ""
    if isinstance(legacy_value, str) and legacy_value.strip():
        return {"configured": True, "scope": "machine", "source": "hardware_config"}

    return {"configured": False, "scope": "none", "source": ""}


def list_study_credential_state(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str,
) -> dict[str, Any]:
    """The full per-study credential picture - never any values."""
    return {
        kind: describe_secret_state(kind, hardware_config, local_secrets, study_id)
        for kind in secret_fields()
    }


def resolve_plugin_secret(
    kind: str,
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str = "",
) -> str:
    """The actual secret value: env > this study's own > machine > legacy config.

    The value twin of `describe_secret_state`, which reports the same chain
    without ever returning what it found.
    """
    declaration = _credential_declarations().get(kind, {})
    field = str(declaration.get("config_field") or "")
    env_var = str(declaration.get("env_var") or "")
    if not field:
        return ""

    env_value = os.getenv(env_var, "").strip() if env_var else ""
    if env_value:
        return env_value

    study_value = get_study_secret(local_secrets, study_id, kind)
    if study_value:
        return study_value

    local_value = local_secrets.get(kind, {}).get(field, "") if isinstance(local_secrets.get(kind), dict) else ""
    if isinstance(local_value, str) and local_value.strip():
        return local_value.strip()

    legacy_value = hardware_config.get(kind, {}).get(field, "") if isinstance(hardware_config.get(kind), dict) else ""
    return legacy_value.strip() if isinstance(legacy_value, str) else ""


def describe_secret_storage_location(
    kind: str,
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    local_secrets_path: Path,
    study_id: str = "",
) -> str:
    """A human-readable answer to "where did this value come from" for a status panel."""
    declaration = _credential_declarations().get(kind, {})
    env_var = str(declaration.get("env_var") or "")
    source = describe_secret_state(kind, hardware_config, local_secrets, study_id)["source"]
    if source == "env":
        return f"Umgebungsvariable {env_var}" if env_var else "Umgebungsvariable"
    if source == "study_file":
        return f"pro Studie in {local_secrets_path.name}"
    if source == "local_file":
        return f"backend-lokal in {local_secrets_path.name}"
    if source == "hardware_config":
        return "legacy in hardware settings"
    return "nicht gespeichert"
