from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..studies.study_secrets_service import describe_secret_state, secret_fields


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
