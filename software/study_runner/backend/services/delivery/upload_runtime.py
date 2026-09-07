"""Bind manifest-declared upload destinations to the persistent job queue."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from study_runner.plugin_framework.plugin_api import Plugin
from study_runner.plugin_framework.registry import (
    build_context,
    get_plugin_manifest,
    get_plugins_with_capability,
)

from ..studies.study_config_service import save_config, save_study
from ..studies.study_plugin_config import normalize_study_settings_plugins
from .upload_jobs_service import UploadJobError, UploadJobService


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
        with app.app_context():
            result = handler(context, payload) or {"ok": True}
        if not isinstance(result, dict):
            raise UploadJobError(
                f"Upload plugin {plugin.key!r} returned an invalid result."
            )
        if result.get("ok") is False:
            detail = str(result.get("error") or "unknown error")
            print(f"[UPLOADS] {destination} attempt failed: {detail}")
            raise UploadJobError(
                f"{plugin.label} is temporarily unavailable; Study Runner will retry."
            )
        updates = result.get("study_config_updates")
        if isinstance(updates, dict) and updates:
            _persist_study_config_updates(app, payload, plugin.key, updates)
        return result

    return execute


def _persist_study_config_updates(
    app: Any,
    payload: dict[str, Any],
    plugin_key: str,
    flat_updates: dict[str, str],
) -> None:
    """Save settings an upload discovered (e.g. an auto-created database id).

    A destination plugin runs inside its own `driver.py` subprocess and may
    not import `backend` (docs/architecture-1.0-umbau.md invariant #2), so it
    cannot save this itself -- it reports what changed via
    `study_config_updates` in its result, using the same flat field names it
    already worked with internally, and this host-side function (which does
    own the dependency) does the actual write. Mirrors the old in-process
    `notion_upload.adapter._persist_study_database_id`: merge the flat
    updates onto a copy of the queued config, canonicalize, and save both the
    active config and the study's own file.
    """
    config_data = payload.get("config_data")
    if not isinstance(config_data, dict):
        return

    try:
        canonical_config = deepcopy(config_data)
        study_settings = canonical_config.setdefault("study_settings", {})
        plugins = study_settings.setdefault("plugins", {})
        plugin_settings = plugins.setdefault(
            plugin_key,
            {"enabled": True, "required": False, "settings": {}},
        )
        settings = plugin_settings.setdefault("settings", {})
        for flat_key, value in flat_updates.items():
            setting_name = flat_key.removeprefix(f"{plugin_key}_")
            settings[setting_name] = value
        canonical_config["study_settings"] = normalize_study_settings_plugins(study_settings)

        save_config(app.config["CONFIG_FILE"], canonical_config)
        save_study(app.config["SAVED_STUDIES_DIR"], canonical_config)
        print(f"[UPLOADS] Persisted {sorted(flat_updates)} to study config for {plugin_key!r}.")
    except Exception as error:
        print(f"[UPLOADS] Could not persist study config updates for {plugin_key!r}: {error}")
