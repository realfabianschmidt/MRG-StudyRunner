from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PLUGIN_API_VERSION = 4
SUPPORTED_PLUGIN_API_VERSIONS = (3, 4)


StatusPayload = dict[str, Any]
InitializeHandler = Callable[["PluginContext"], None]
RuntimeActionHandler = Callable[["PluginContext"], Any]
AdminActionHandler = Callable[["PluginContext", str, dict[str, Any]], Any]
ParticipantActionHandler = Callable[["PluginContext", str, dict[str, Any]], Any]
ParticipantIngestHandler = Callable[["PluginContext", str, dict[str, Any]], Any]
TrialHandler = Callable[["PluginContext", dict[str, Any]], None]
MarkerHandler = Callable[["PluginContext", dict[str, Any]], None]
IntervalSummaryHandler = Callable[["PluginContext", float, float], dict[str, Any]]
IntervalExportHandler = Callable[["PluginContext", float, float], list[dict[str, Any]]]
StatusHandler = Callable[["PluginContext"], StatusPayload]
UploadDestinationHandler = Callable[["PluginContext", dict[str, Any]], dict[str, Any]]
StudySettingValidator = Callable[[str, str], None]
ConsoleLineHandler = Callable[["PluginContext", str], Any]


@dataclass(frozen=True)
class PluginContext:
    """Runtime data shared with the built-in plugins.

    Plugins receive this object instead of importing Flask globals. That keeps each
    plugin easy to test and makes the boundary between persisted config,
    backend-local secrets, and runtime adapter state explicit.
    """

    base_dir: Path
    data_dir: Path
    hardware_config: dict[str, Any]
    local_secrets: dict[str, Any]
    local_secrets_file: Path
    runtime_locked: bool = False
    persist_hardware_config: Callable[[dict[str, Any]], None] | None = None

    def resolve_platform_value(self, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        for key in self.platform_keys():
            selected = value.get(key)
            if selected not in (None, ""):
                return selected

        for fallback_key in ("default", "windows", "macos", "linux"):
            selected = value.get(fallback_key)
            if selected not in (None, ""):
                return selected

        return None

    def resolve_project_path(self, value: str | None) -> str | None:
        if not value:
            return None

        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        return str(path.resolve())

    def secret(self, plugin_key: str, study_id: str = "") -> str:
        """This plugin's declared secret, resolved env > study > machine > legacy.

        `plugin_key` is also the `kind` its `credentials` capability is filed
        under - see `study_secrets_service.py`, the single home for how a
        secret is stored and found regardless of which plugin owns it.
        """
        from study_runner.backend.services.studies.study_secrets_service import resolve_plugin_secret

        return resolve_plugin_secret(plugin_key, self.hardware_config, self.local_secrets, study_id)

    @staticmethod
    def platform_keys() -> tuple[str, ...]:
        if os.name == "nt":
            return ("windows", "win32", "default")
        if sys.platform == "darwin":
            return ("macos", "mac", "darwin", "default")
        return ("linux", "posix", "default")


@dataclass(frozen=True)
class Plugin:
    """What one built-in plugin offers: its identity and its handlers."""

    key: str
    label: str
    category: str
    config_key: str
    can_start: bool = False
    can_stop: bool = False
    can_restart: bool = False
    can_toggle: bool = True
    has_lsl: bool = False
    has_recording: bool = False
    initialize: InitializeHandler | None = None
    get_status: StatusHandler | None = None
    start: RuntimeActionHandler | None = None
    stop: RuntimeActionHandler | None = None
    restart: RuntimeActionHandler | None = None
    run_admin_action: AdminActionHandler | None = None
    run_participant_action: ParticipantActionHandler | None = None
    ingest_participant: ParticipantIngestHandler | None = None
    on_trial_start: TrialHandler | None = None
    on_trial_stop: TrialHandler | None = None
    on_trial_marker: MarkerHandler | None = None
    get_interval_summary: IntervalSummaryHandler | None = None
    export_interval_samples: IntervalExportHandler | None = None
    publish_destination: UploadDestinationHandler | None = None
    validate_study_setting: StudySettingValidator | None = None
    handle_console_line: ConsoleLineHandler | None = None
    sidecar_sensor: str | None = None
    sidecar_filename_suffix: str | None = None
    sidecar_output_key: str | None = None
