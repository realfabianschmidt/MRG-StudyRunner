"""Bind manifest-declared upload destinations to the persistent job queue."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from study_runner.contracts.plugin_api import Plugin
from study_runner.plugin_framework.registry import (
    build_context,
    get_plugin_manifest,
    get_plugins_with_capability,
)
from study_runner.shared.atomic_io import atomic_path_lock, atomic_write_json
from study_runner.shared.study_identifiers import normalize_study_id

from ..studies.study_config_service import patch_study_plugin_settings
from ..studies.study_plugin_config import normalize_study_settings_plugins
from .upload_jobs_service import PermanentUploadError, UploadJobError, UploadJobService


def configure_upload_jobs(app) -> UploadJobService:
    """Register every valid ``upload_destination`` plugin without a key list."""

    service = UploadJobService(Path(app.config["DATA_DIR"]))
    for plugin in get_plugins_with_capability("upload_destination"):
        capability = (
            (get_plugin_manifest(plugin.key).get("capability_config") or {})
            .get("upload_destination", {})
        )
        destination = str(capability.get("destination") or plugin.key).strip()
        service.register_executor(destination, _plugin_executor(app, plugin, destination))

    migration = service.migrate_legacy_notion_queue()
    if migration.get("migrated"):
        print(f"[UPLOADS] Migrated {migration['migrated']} legacy Notion queue entries.")
    if migration.get("error"):
        print(f"[UPLOADS] Legacy Notion queue migration needs attention: {migration['error']}")
    app.config["UPLOAD_JOBS_SERVICE"] = service
    return service


def _plugin_executor(
    app: Any,
    plugin: Plugin,
    destination: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    manifest = get_plugin_manifest(plugin.key)
    capability = (manifest.get("capability_config") or {}).get("upload_destination") or {}
    schema = manifest.get("study_settings_schema") or {}
    allowed_updates = set(capability.get("discovered_settings") or [])
    if any(schema.get(key, {}).get("type") != "string" for key in allowed_updates):
        raise ValueError("Discovered destination settings must be declared study string fields.")
    defaults = {key: field.get("default") for key, field in schema.items() if "default" in field}

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        handler = plugin.publish_destination
        if handler is None:
            raise UploadJobError(
                f"Upload plugin {plugin.key!r} has no destination handler."
            )
        context = build_context(
            base_dir=app.config["BASE_DIR"],
            data_dir=app.config["DATA_DIR"],
            hardware_config=app.config.get("HARDWARE_CONFIG", {}) or {},
            local_secrets=app.config.get("LOCAL_SECRETS", {}) or {},
            local_secrets_file=app.config["LOCAL_SECRETS_FILE"],
        )

        def publish(attempt: dict[str, Any]) -> dict[str, Any]:
            with app.app_context():
                result = handler(context, attempt) or {"ok": True}
            if not isinstance(result, dict):
                raise UploadJobError(f"Upload plugin {plugin.key!r} returned an invalid result.")
            return result

        if allowed_updates:
            config = payload.get("config_data") or {}
            study_id = str(config.get("study_id") or "").strip()
            if not study_id:
                raise UploadJobError("A study ID is required to remember discovered upload targets.")
            selection = normalize_study_settings_plugins(config.get("study_settings"))["plugins"][plugin.key]
            base_settings = {**defaults, **selection["settings"]}
            identity = json.dumps(
                [normalize_study_id(study_id), plugin.key, base_settings],
                sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")
            state_path = Path(app.config["DATA_DIR"]) / "upload_targets" / f"{hashlib.sha256(identity).hexdigest()}.json"

            # Serialize attempts sharing one original target. The durable file
            # contains only permitted discoveries; scientific snapshots and
            # credentials are never copied into it.
            with atomic_path_lock(state_path):
                known: dict[str, str] = {}
                if state_path.is_file():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if not isinstance(state, dict) or state.get("schema") != "study-runner/upload-target/v1":
                        raise UploadJobError("Stored upload target has an unsupported schema.")
                    known = _validate_updates(state.get("settings"), allowed_updates, schema)
                attempt = deepcopy(payload)
                attempt_selection = deepcopy(selection)
                attempt_selection["settings"].update(known)
                attempt["config_data"].setdefault("study_settings", {}).setdefault("plugins", {})[plugin.key] = attempt_selection
                result = publish(attempt)
                discovered = _validate_updates(result.get("study_config_updates", {}), allowed_updates, schema)
                if discovered:
                    known.update(discovered)
                    # Checkpoint before evaluating ok: the target may have been
                    # created successfully even though the later upload failed.
                    atomic_write_json(state_path, {"schema": "study-runner/upload-target/v1", "settings": known})
                if known:
                    _persist_study_config_updates(
                        app, study_id, plugin.key, base_settings, known, defaults,
                    )
        else:
            result = publish(deepcopy(payload))
            _validate_updates(result.get("study_config_updates", {}), allowed_updates, schema)

        if result.get("ok") is False:
            detail = str(result.get("error") or "unknown error")
            print(f"[UPLOADS] {destination} attempt failed: {detail}")
            if result.get("permanent") is True:
                # Wrong credentials, an inaccessible target, or a missing
                # setting: retrying cannot help, so say exactly what to fix.
                raise PermanentUploadError(f"{plugin.label}: {detail}")
            raise UploadJobError(f"{plugin.label}: {detail} (Study Runner will retry automatically.)")
        return result

    return execute


def _validate_updates(value: Any, allowed: set[str], schema: dict[str, Any]) -> dict[str, str]:
    """A plugin can report only its explicitly permitted nonempty string fields."""
    if not isinstance(value, dict):
        raise UploadJobError("Discovered destination settings must be an object.")
    for key, setting in value.items():
        if (
            key not in allowed
            or not isinstance(setting, str)
            or not setting.strip()
            or len(setting) > min(4096, schema.get(key, {}).get("max_length", 4096))
        ):
            raise UploadJobError("Plugin reported an invalid or undeclared destination setting.")
    return dict(value)


def _persist_study_config_updates(
    app: Any,
    study_id: str,
    plugin_key: str,
    expected_settings: dict[str, Any],
    updates: dict[str, str],
    defaults: dict[str, Any],
) -> None:
    """Refresh a matching study after the durable retry state was committed."""
    try:
        patch_study_plugin_settings(
            app.config["CONFIG_FILE"], app.config["SAVED_STUDIES_DIR"],
            study_id=study_id, plugin_key=plugin_key, expected_settings=expected_settings,
            updates=updates, defaults=defaults,
        )
    except Exception as error:
        # The target checkpoint remains available to the next queued upload.
        print(f"[UPLOADS] Could not refresh study settings for {plugin_key!r}: {error}")
