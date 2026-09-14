"""One-way compatibility migrations for machine settings."""
from __future__ import annotations

import re
from typing import Any


_PLUGIN_CATEGORIES = {
    "brainbit": "sensors",
    "camera_emotion": "sensors",
    "mr60_mini_radar": "sensors",
    "nextcloud_upload": "destinations",
    "notion_upload": "destinations",
    "osc_touchdesigner": "outputs",
    "choice": "cards",
    "finish": "cards",
    "likert": "cards",
    "mood_meter": "cards",
    "multi_slider": "cards",
    "participant_id": "cards",
    "ranking": "cards",
    "semantic": "cards",
    "slider": "cards",
    "stimulus": "cards",
    "text": "cards",
    "word_cloud": "cards",
}


def migrate_moved_plugin_paths(value: Any) -> tuple[Any, int]:
    """Move stored plugin paths to ``plugins/<category>/<name>`` recursively."""

    if isinstance(value, str):
        migrated = value
        migrated = re.sub(
            r"study_runner([/\\])extensions\1(sensors|cards|destinations|outputs)\1([A-Za-z0-9_.-]+)",
            lambda match: (
                f"study_runner{match.group(1)}plugins{match.group(1)}"
                f"{match.group(2)}{match.group(1)}{match.group(3)}"
            ),
            migrated,
        )
        for name, category in _PLUGIN_CATEGORIES.items():
            for separator in ("/", "\\"):
                target = separator.join(("study_runner", "plugins", category, name))
                sources = (
                    separator.join(("study_runner", "integrations", name)),
                    separator.join(("study_runner", "plugins", name)),
                )
                for source in sources:
                    migrated = re.sub(
                        re.escape(source) + r"(?=$|[/\\])",
                        lambda _match, replacement=target: replacement,
                        migrated,
                    )
        return migrated, int(migrated != value)

    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        changes = 0
        for key, item in value.items():
            result[key], changed = migrate_moved_plugin_paths(item)
            changes += changed
        return result, changes

    if isinstance(value, list):
        migrated_items = [migrate_moved_plugin_paths(item) for item in value]
        return [item for item, _changed in migrated_items], sum(
            changed for _item, changed in migrated_items
        )

    return value, 0
