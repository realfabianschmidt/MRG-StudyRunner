"""Bind persistent upload jobs to the Flask app and network integrations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .nextcloud_service import NextcloudPublicShareClient
from .secrets_service import resolve_nextcloud_password
from .upload_jobs_service import UploadJobError, UploadJobService


def configure_upload_jobs(app) -> UploadJobService:
    """Create the app-owned service and register both network executors."""
    service = UploadJobService(Path(app.config["DATA_DIR"]))

    def execute_notion(payload: dict[str, Any]) -> dict[str, Any]:
        from study_runner.integrations.notion_upload import adapter as notion_adapter

        with app.app_context():
            result = notion_adapter.upload_study_result(
                result_payload=payload.get("result_payload") or {},
                hardware_config=payload.get("hardware_config") or {},
                saved_output=payload.get("saved_output") or {},
                config_data=payload.get("config_data") or {},
                is_retry=True,
            )
        if not result.get("ok"):
            detail = str(result.get("error") or "unknown error")
            print(f"[UPLOADS] Notion attempt failed: {detail}")
            raise UploadJobError("Notion upload is temporarily unavailable; Study Runner will retry.")
        return result

    def execute_nextcloud(payload: dict[str, Any]) -> dict[str, Any]:
        config_data = payload.get("config_data") or {}
        settings = config_data.get("study_settings") or {}
        share_link = str(settings.get("nextcloud_share_link") or "").strip()
        if not settings.get("nextcloud_enabled") or not share_link:
            raise UploadJobError("Nextcloud is no longer configured for this study.")

        hardware_config = app.config.get("HARDWARE_CONFIG", {}) or {}
        local_secrets = app.config.get("LOCAL_SECRETS", {}) or {}
        nextcloud_config = hardware_config.get("nextcloud") or {}
        password = resolve_nextcloud_password(hardware_config, local_secrets)
        timeout_seconds = int(nextcloud_config.get("timeout_seconds") or 30)

        saved_output = payload.get("saved_output") or {}
        relative_folder = str(saved_output.get("participant_dir") or "")
        root = Path(app.config["DATA_DIR"]).resolve()
        local_folder = (root.parent / relative_folder).resolve()
        if not relative_folder or not local_folder.is_relative_to(root):
            raise UploadJobError("The saved session folder is outside the results directory.")

        result_payload = payload.get("result_payload") or {}
        return NextcloudPublicShareClient(
            share_link,
            password=password,
            timeout_seconds=timeout_seconds,
        ).upload_session_folder(
            local_folder,
            study_id=str(result_payload.get("study_id") or ""),
            participant_id=str(result_payload.get("participant_id") or ""),
        )

    service.register_executor("notion", execute_notion)
    service.register_executor("nextcloud", execute_nextcloud)
    migration = service.migrate_legacy_notion_queue()
    if migration.get("migrated"):
        print(f"[UPLOADS] Migrated {migration['migrated']} legacy Notion queue entries.")
    if migration.get("error"):
        print(f"[UPLOADS] Legacy Notion queue migration needs attention: {migration['error']}")
    app.config["UPLOAD_JOBS_SERVICE"] = service
    return service
