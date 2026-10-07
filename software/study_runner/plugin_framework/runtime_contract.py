"""The operations a plugin process may handle, derived from its manifest."""

from __future__ import annotations

from typing import Any, Mapping

_HANDLERS = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "admin_action": "run_admin_action",
    "participant_action": "run_participant_action",
    "participant_ingest": "ingest_participant",
    "trial_start": "on_trial_start",
    "trial_stop": "on_trial_stop",
    "trial_marker": "on_trial_marker",
    "session_end": "on_session_end",
    "interval_summary": "get_interval_summary",
    "interval_export": "export_interval_samples",
    "publish": "publish_destination",
    "card_defaults": "get_card_defaults",
    "card_normalize": "normalize_card_config",
}


def declared_operations(manifest: Mapping[str, Any]) -> set[str]:
    """Use capabilities and runtime events as the only operation allowlist."""
    capabilities = manifest.get("capabilities") or {}
    names = set(capabilities) if isinstance(capabilities, (dict, list)) else set()
    runtime = manifest.get("runtime") or {}
    operations = {"initialize", "status", "shutdown"}
    operations.update(runtime.get("actions") or [])
    for event in runtime.get("trial_events") or []:
        operations.add("session_end" if event == "session_end" else f"trial_{event}")
    for capability, operation in (
        ("admin_actions", "admin_action"),
        ("participant_actions", "participant_action"),
        ("participant_ingest", "participant_ingest"),
        ("interval_summary", "interval_summary"),
        ("sidecar_export", "interval_export"),
        ("upload_destination", "publish"),
    ):
        if capability in names:
            operations.add(operation)
    if "card_contract" in names:
        operations.update({"card_defaults", "card_normalize", "card_validate_answer"})
    settings = manifest.get("settings") or {}
    study = settings.get("study") or manifest.get("study_settings_schema") or {}
    if any(isinstance(field, Mapping) and field.get("format") for field in study.values()):
        operations.add("validate_study_setting")
    return operations


def validate_plugin_callbacks(plugin: Any, manifest: Mapping[str, Any]) -> None:
    """Fail process startup when a declared operation has no implementation."""
    operations = declared_operations(manifest)
    missing = [
        f"{operation} ({handler})"
        for operation, handler in _HANDLERS.items()
        if operation in operations and not callable(getattr(plugin, handler, None))
    ]
    capabilities = manifest.get("capabilities") or {}
    if "health" in capabilities and not callable(getattr(plugin, "get_status", None)):
        missing.append("status (get_status)")
    card = capabilities.get("card_contract") if isinstance(capabilities, Mapping) else None
    if "card_validate_answer" in operations and card:
        answerless = set(card.get("answerless_types") or [])
        if set(card.get("question_types") or []) - answerless and not callable(plugin.validate_card_answer):
            missing.append("card_validate_answer (validate_card_answer)")
    if "validate_study_setting" in operations and not callable(plugin.validate_study_setting):
        missing.append("validate_study_setting (validate_study_setting)")
    if missing:
        raise TypeError(f"Plugin '{plugin.key}' declares missing handlers: {', '.join(missing)}")
