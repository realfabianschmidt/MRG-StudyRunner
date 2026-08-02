from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_json
from .study_secrets_service import describe_secret_state, get_study_secret


NOTION_API_KEY_ENV = "STUDY_RUNNER_NOTION_API_KEY"
NEXTCLOUD_PASSWORD_ENV = "STUDY_RUNNER_NEXTCLOUD_PASSWORD"

SECRET_ENV_VARS = {
    "notion": NOTION_API_KEY_ENV,
    "nextcloud": NEXTCLOUD_PASSWORD_ENV,
}


def load_local_secrets(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_local_secrets(path: Path, secrets: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(secrets, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_notion_api_key(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str = "",
) -> str:
    """env > this study's own key > machine key > legacy hardware config."""
    env_value = os.getenv(NOTION_API_KEY_ENV, "").strip()
    if env_value:
        return env_value

    study_value = get_study_secret(local_secrets, study_id, "notion")
    if study_value:
        return study_value

    local_value = (
        local_secrets.get("notion", {}).get("api_key", "")
        if isinstance(local_secrets.get("notion"), dict)
        else ""
    )
    if isinstance(local_value, str) and local_value.strip():
        return local_value.strip()

    legacy_value = hardware_config.get("notion", {}).get("api_key", "")
    if isinstance(legacy_value, str):
        return legacy_value.strip()
    return ""


def describe_notion_api_key_source(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str = "",
) -> str:
    if os.getenv(NOTION_API_KEY_ENV, "").strip():
        return "env"

    if get_study_secret(local_secrets, study_id, "notion"):
        return "study_file"

    local_value = (
        local_secrets.get("notion", {}).get("api_key", "")
        if isinstance(local_secrets.get("notion"), dict)
        else ""
    )
    if isinstance(local_value, str) and local_value.strip():
        return "local_file"

    legacy_value = hardware_config.get("notion", {}).get("api_key", "")
    if isinstance(legacy_value, str) and legacy_value.strip():
        return "hardware_config"
    return ""


def describe_notion_api_key_storage(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    local_secrets_path: Path,
    study_id: str = "",
) -> str:
    source = describe_notion_api_key_source(hardware_config, local_secrets, study_id)
    if source == "env":
        return f"Umgebungsvariable {NOTION_API_KEY_ENV}"
    if source == "study_file":
        return f"pro Studie in {local_secrets_path.name}"
    if source == "local_file":
        return f"backend-lokal in {local_secrets_path.name}"
    if source == "hardware_config":
        return "legacy in hardware settings"
    return "nicht gespeichert"


def resolve_nextcloud_password(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str = "",
) -> str:
    """env > this study's own password > machine password > legacy config."""
    env_value = os.getenv(NEXTCLOUD_PASSWORD_ENV, "")
    if env_value:
        return env_value

    study_value = get_study_secret(local_secrets, study_id, "nextcloud")
    if study_value:
        return study_value

    local_value = (
        local_secrets.get("nextcloud", {}).get("password", "")
        if isinstance(local_secrets.get("nextcloud"), dict)
        else ""
    )
    if isinstance(local_value, str) and local_value:
        return local_value

    legacy_value = hardware_config.get("nextcloud", {}).get("password", "")
    return legacy_value if isinstance(legacy_value, str) else ""


def redact_hardware_config(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
    study_id: str = "",
) -> dict[str, Any]:
    """Strip every secret, keeping only "is one configured, and from where".

    `*_scope` is added alongside the existing `*_configured` / `*_source` keys
    rather than replacing them, so nothing that already reads this payload has
    to change.
    """
    redacted = deepcopy(hardware_config)
    notion_config = redacted.get("notion")
    if isinstance(notion_config, dict):
        state = describe_secret_state(
            "notion", hardware_config, local_secrets, study_id, env_var=NOTION_API_KEY_ENV
        )
        notion_config["api_key"] = ""
        notion_config["api_key_configured"] = state["configured"]
        notion_config["api_key_source"] = state["source"]
        notion_config["api_key_scope"] = state["scope"]

    nextcloud_config = redacted.get("nextcloud")
    if isinstance(nextcloud_config, dict):
        state = describe_secret_state(
            "nextcloud", hardware_config, local_secrets, study_id, env_var=NEXTCLOUD_PASSWORD_ENV
        )
        nextcloud_config["password"] = ""
        nextcloud_config["password_configured"] = state["configured"]
        nextcloud_config["password_source"] = state["source"]
        nextcloud_config["password_scope"] = state["scope"]
    return redacted
