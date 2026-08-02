"""Bind manifest-declared upload destinations to the persistent job queue."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from study_runner.integrations.plugin_api import IntegrationPlugin
from study_runner.integrations.registry import (
    build_context,
    get_plugin_manifest,
    get_plugins_with_capability,
)

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
    plugin: IntegrationPlugin,
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
        return result

    return execute
