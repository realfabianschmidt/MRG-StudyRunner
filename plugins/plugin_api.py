from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


StatusPayload = dict[str, Any]
InitializeHandler = Callable[["IntegrationContext"], None]
RuntimeActionHandler = Callable[["IntegrationContext"], Any]
TrialHandler = Callable[["IntegrationContext", dict[str, Any]], None]
IntervalSummaryHandler = Callable[["IntegrationContext", float, float], dict[str, Any]]
IntervalExportHandler = Callable[["IntegrationContext", float, float], list[dict[str, Any]]]
StatusHandler = Callable[["IntegrationContext"], StatusPayload]


@dataclass(frozen=True)
class IntegrationContext:
    """Runtime data shared with built-in integration plugins.

    Plugins receive this object instead of importing Flask globals. That keeps each
    connector easy to test and makes the boundary between persisted config,
    backend-local secrets, and runtime adapter state explicit.
    """

    base_dir: Path
    data_dir: Path
    hardware_config: dict[str, Any]
    local_secrets: dict[str, Any]
    local_secrets_file: Path

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

    def notion_api_key(self) -> str:
        from server_app.services.secrets_service import resolve_notion_api_key

        return resolve_notion_api_key(self.hardware_config, self.local_secrets)

    @staticmethod
    def platform_keys() -> tuple[str, ...]:
        if os.name == "nt":
            return ("windows", "win32", "default")
        if sys.platform == "darwin":
            return ("macos", "mac", "darwin", "default")
        return ("linux", "posix", "default")


@dataclass(frozen=True)
class IntegrationPlugin:
    """Definition of one built-in connector shown in the Admin Dashboard."""

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
    on_trial_start: TrialHandler | None = None
    on_trial_stop: TrialHandler | None = None
    get_interval_summary: IntervalSummaryHandler | None = None
    export_interval_samples: IntervalExportHandler | None = None
    sidecar_sensor: str | None = None
    sidecar_filename_suffix: str | None = None
    sidecar_output_key: str | None = None
