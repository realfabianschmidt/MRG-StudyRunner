from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


NOTION_API_KEY_ENV = "STUDY_RUNNER_NOTION_API_KEY"


def load_local_secrets(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_local_secrets(path: Path, secrets: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(secrets, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_notion_api_key(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
) -> str:
    env_value = os.getenv(NOTION_API_KEY_ENV, "").strip()
    if env_value:
        return env_value

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
) -> str:
    if os.getenv(NOTION_API_KEY_ENV, "").strip():
        return "env"

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
) -> str:
    source = describe_notion_api_key_source(hardware_config, local_secrets)
    if source == "env":
        return f"Umgebungsvariable {NOTION_API_KEY_ENV}"
    if source == "local_file":
        return f"backend-lokal in {local_secrets_path.name}"
    if source == "hardware_config":
        return "legacy in hardware_config.json"
    return "nicht gespeichert"


def redact_hardware_config(
    hardware_config: dict[str, Any],
    local_secrets: dict[str, Any],
) -> dict[str, Any]:
    redacted = deepcopy(hardware_config)
    notion_config = redacted.get("notion")
    if not isinstance(notion_config, dict):
        return redacted

    notion_config["api_key"] = ""
    notion_config["api_key_configured"] = bool(
        resolve_notion_api_key(hardware_config, local_secrets)
    )
    notion_config["api_key_source"] = describe_notion_api_key_source(
        hardware_config,
        local_secrets,
    )
    return redacted
