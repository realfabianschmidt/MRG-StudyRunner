"""Small manifest-to-finalization contract for upload destination plugins.

The plugin owns network execution.  This module owns only stable orchestration
metadata: the step key, legacy read aliases, and scientific/recovery policy.
Definitions are persisted with each finalization job so replay does not depend
on whatever plugin version happens to be installed after a restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class DestinationPluginDefinition:
    plugin_key: str
    destination: str
    label: str
    default_enabled: bool = False
    requires_valid_result: bool = True
    publish_on_attention: bool = False
    republish_on_degraded: bool = False
    purge_verified_sources: bool = False
    legacy_enabled_field: str = ""
    legacy_settings: tuple[tuple[str, str], ...] = ()

    @property
    def step_key(self) -> str:
        return f"publish_{self.plugin_key}"

    def enabled_for(self, study_settings: Mapping[str, Any]) -> bool:
        plugins = study_settings.get("plugins")
        selection = plugins.get(self.plugin_key) if isinstance(plugins, Mapping) else None
        if isinstance(selection, Mapping) and "enabled" in selection:
            return bool(selection.get("enabled"))
        if self.legacy_enabled_field and self.legacy_enabled_field in study_settings:
            return bool(study_settings.get(self.legacy_enabled_field))
        return self.default_enabled

    def policy(self) -> dict[str, bool]:
        return {
            "requires_valid_result": self.requires_valid_result,
            "publish_on_attention": self.publish_on_attention,
            "republish_on_degraded": self.republish_on_degraded,
            "purge_verified_sources": self.purge_verified_sources,
        }

    def persisted(self) -> dict[str, Any]:
        return {
            "plugin_key": self.plugin_key,
            "destination": self.destination,
            "step_key": self.step_key,
            "label": self.label,
            "policy": self.policy(),
        }

    @classmethod
    def from_persisted(cls, value: Mapping[str, Any]) -> "DestinationPluginDefinition":
        policy = value.get("policy")
        policy = policy if isinstance(policy, Mapping) else {}
        return cls(
            plugin_key=str(value.get("plugin_key") or "").strip(),
            destination=str(value.get("destination") or value.get("plugin_key") or "").strip(),
            label=str(value.get("label") or value.get("plugin_key") or "Upload").strip(),
            requires_valid_result=bool(policy.get("requires_valid_result", True)),
            publish_on_attention=bool(policy.get("publish_on_attention", False)),
            republish_on_degraded=bool(policy.get("republish_on_degraded", False)),
            purge_verified_sources=bool(policy.get("purge_verified_sources", False)),
        )


def destination_definitions_from_manifests(
    manifests: Mapping[str, Mapping[str, Any]],
) -> tuple[DestinationPluginDefinition, ...]:
    definitions: list[tuple[int, DestinationPluginDefinition]] = []
    for plugin_key, manifest in manifests.items():
        capabilities = set(manifest.get("capabilities") or [])
        if "upload_destination" not in capabilities:
            continue
        capability = (
            (manifest.get("capability_config") or {}).get("upload_destination") or {}
        )
        legacy = capability.get("legacy")
        legacy = legacy if isinstance(legacy, Mapping) else {}
        raw_legacy_settings = legacy.get("settings")
        raw_legacy_settings = (
            raw_legacy_settings if isinstance(raw_legacy_settings, Mapping) else {}
        )
        ui = manifest.get("ui")
        ui = ui if isinstance(ui, Mapping) else {}
        definitions.append(
            (
                int(ui.get("order") or 1_000),
                DestinationPluginDefinition(
                    plugin_key=str(plugin_key),
                    destination=str(capability.get("destination") or plugin_key),
                    label=str(ui.get("label") or plugin_key),
                    default_enabled=bool(capability.get("default_enabled", False)),
                    requires_valid_result=bool(
                        capability.get("requires_valid_result", True)
                    ),
                    publish_on_attention=bool(
                        capability.get("publish_on_attention", False)
                    ),
                    republish_on_degraded=bool(
                        capability.get("republish_on_degraded", False)
                    ),
                    purge_verified_sources=bool(
                        capability.get("purge_verified_sources", False)
                    ),
                    legacy_enabled_field=str(legacy.get("enabled_field") or ""),
                    legacy_settings=tuple(
                        (str(name), str(field))
                        for name, field in sorted(raw_legacy_settings.items())
                    ),
                ),
            )
        )
    definitions.sort(key=lambda item: (item[0], item[1].plugin_key))
    return validate_destination_definitions(item[1] for item in definitions)


def installed_destination_definitions() -> tuple[DestinationPluginDefinition, ...]:
    from study_runner.plugin_framework.registry import get_plugin_manifests

    return destination_definitions_from_manifests(get_plugin_manifests())


def validate_destination_definitions(
    values: Iterable[DestinationPluginDefinition],
) -> tuple[DestinationPluginDefinition, ...]:
    definitions = tuple(values)
    plugin_keys = [item.plugin_key for item in definitions]
    destinations = [item.destination for item in definitions]
    step_keys = [item.step_key for item in definitions]
    for label, items in (
        ("plugin key", plugin_keys),
        ("destination", destinations),
        ("finalization step", step_keys),
    ):
        duplicates = sorted({item for item in items if items.count(item) > 1})
        if duplicates:
            raise ValueError(f"Duplicate upload {label}: {', '.join(duplicates)}")
    if any(not item for item in (*plugin_keys, *destinations)):
        raise ValueError("Upload destination definitions require non-empty keys.")
    purge_destinations = [
        item.plugin_key for item in definitions if item.purge_verified_sources
    ]
    if len(purge_destinations) > 1:
        raise ValueError(
            "Only one upload destination may declare purge_verified_sources: "
            + ", ".join(purge_destinations)
        )
    return definitions


def definitions_from_state(
    state: Mapping[str, Any],
    fallback: Iterable[DestinationPluginDefinition],
) -> tuple[DestinationPluginDefinition, ...]:
    raw = state.get("destinations")
    if isinstance(raw, list):
        parsed = tuple(
            DestinationPluginDefinition.from_persisted(item)
            for item in raw
            if isinstance(item, Mapping)
        )
        if parsed:
            return validate_destination_definitions(parsed)
    # Compatibility for finalization journals created before definitions were
    # embedded. Keep only definitions whose historic step is actually present.
    step_keys = {
        str(step.get("key") or "")
        for step in state.get("steps", [])
        if isinstance(step, Mapping)
    }
    return tuple(item for item in fallback if item.step_key in step_keys)
