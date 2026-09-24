"""Are the sensors this study needs connected right now?

study_readiness_service.py deliberately checks stored configuration only.
This is its live counterpart for the moment the admin presses Play: it reads
the last known health of every sensor plugin the study selects (from the
sensor coordinator's non-blocking status cache) and names each one that is
not delivering data. The admin is warned and may still start on purpose.

Generic by design: plugins are found through their ``study_sensor``
capability and the study's own plugin selection, never by name.
"""
from __future__ import annotations

from typing import Any, Mapping

# Statuses a sensor plugin reports while it is actually delivering data.
LIVE_STATUSES = frozenset({"connected", "ready", "receiving", "streaming", "recording"})


def selected_study_sensors(config_data: Mapping[str, Any], manifests: Mapping[str, Any]) -> list[str]:
    settings = config_data.get("study_settings") or {}
    selections = settings.get("plugins") if isinstance(settings, Mapping) else {}
    if not isinstance(selections, Mapping):
        return []
    keys = []
    for plugin_key, selection in selections.items():
        if not isinstance(selection, Mapping) or not selection.get("enabled"):
            continue
        capabilities = (manifests.get(plugin_key) or {}).get("capabilities") or {}
        if "study_sensor" in capabilities:
            keys.append(str(plugin_key))
    return keys


def live_sensor_issues(
    config_data: Mapping[str, Any],
    plugin_statuses: Mapping[str, Any],
    manifests: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """One entry per selected sensor that is not live: ``{plugin, label, status, problem}``."""
    issues = []
    for plugin_key in selected_study_sensors(config_data, manifests):
        manifest = manifests.get(plugin_key) or {}
        label = str((manifest.get("ui") or {}).get("label") or plugin_key)
        status = plugin_statuses.get(plugin_key)
        if not isinstance(status, Mapping):
            issues.append({"plugin": plugin_key, "label": label, "status": "unknown", "problem": "No status reported yet."})
            continue
        value = str(status.get("status") or "unknown").strip().lower()
        if value in LIVE_STATUSES:
            continue
        problem = str(
            status.get("last_error")
            or status.get("error")
            or status.get("last_message")
            or status.get("message")
            or ""
        ).strip()
        issues.append({"plugin": plugin_key, "label": label, "status": value, "problem": problem})
    return issues
