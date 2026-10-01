"""Which software produced a session -- what a methods section cites.

Captured when the session's finalization job is created, like the destination
definitions: a later update or restart can never change what a session says
about itself. Written into ``meta/manifest.json`` as ``provenance.software``:

    {"study_runner_version": "1.6.1",
     "plugins": {"am_hub": {"version": "3.0.1", "role": "recording"},
                 "mood_meter": {"version": "1.1.0", "role": "card"},
                 "notion": {"version": "1.0.0", "role": "destination"}}}

Recording plugins take the version from the session's recording contract (the
version that actually ran); cards and destinations the installed version at
submission. Core recording sources (markers, clock diagnostics) are part of
Study Runner and covered by its version. ``"partial": true`` marks a session
for which only part of this is known (recorded before version tracking).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from study_runner.data_core.host.recording_dependencies import INTERNAL_RECORDING_SOURCE_KEYS
from study_runner.version import __version__

ROLES = ("recording", "card", "destination")


def installed_plugin_manifests() -> Mapping[str, Mapping[str, Any]]:
    from study_runner.plugin_framework.registry import get_plugin_manifests

    return get_plugin_manifests()


def capture_session_software(
    *,
    recording_plan_file: Path,
    config_data: Mapping[str, Any],
    destination_keys: Iterable[str],
    manifests: Callable[[], Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """At submission. Provenance is reported, never a reason to lose a session."""
    try:
        return session_software_provenance(
            config_data=config_data,
            recording_plan=_optional_json(recording_plan_file),
            destination_keys=destination_keys,
            manifests=manifests(),
        )
    except Exception as error:
        print(f"[FINALIZATION] Could not determine software provenance: {error}")
        return {"study_runner_version": __version__, "plugins": {}, "partial": True}


def recorded_session_software(recording_plan_file: Path) -> dict[str, Any] | None:
    """For a job created before version tracking: only what the session recorded.

    Never today's card or destination versions -- such a job may finish long
    after an update (e.g. once a quality warning is accepted).
    """
    try:
        return legacy_software_provenance(_optional_json(recording_plan_file))
    except Exception as error:
        print(f"[FINALIZATION] Could not determine software provenance: {error}")
        return None


def session_software_provenance(
    *,
    config_data: Mapping[str, Any] | None,
    recording_plan: Mapping[str, Any] | None,
    destination_keys: Iterable[str],
    manifests: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    plugins = _recording_plugins(recording_plan)

    question_types = {
        str(question.get("type"))
        for question in (config_data or {}).get("questions") or []
        if isinstance(question, Mapping)
    }
    for key, manifest in sorted(manifests.items()):
        card_contract = (manifest.get("capability_config") or {}).get("card_contract") or {}
        if question_types & set(card_contract.get("question_types") or []):
            plugins.setdefault(key, {"version": _version(manifest), "role": "card"})

    for key in destination_keys:
        if key in manifests:
            plugins.setdefault(key, {"version": _version(manifests[key]), "role": "destination"})

    plan = recording_plan if isinstance(recording_plan, Mapping) else {}
    return {
        "study_runner_version": str(plan.get("study_runner_version") or __version__),
        "plugins": dict(sorted(plugins.items(), key=lambda item: (ROLES.index(item[1]["role"]), item[0]))),
    }


def legacy_software_provenance(recording_plan: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """What a session recorded before ``provenance.software`` existed.

    Only the recording plugins (from the recording contract) and, once the
    recording plan carried it, the Study Runner version are known, so the result is marked ``partial``;
    ``None`` when the session recorded neither.
    """
    plugins = _recording_plugins(recording_plan)
    plan = recording_plan if isinstance(recording_plan, Mapping) else {}
    version = str(plan.get("study_runner_version") or "")
    if not plugins and not version:
        return None
    return {"study_runner_version": version or None, "plugins": plugins, "partial": True}


def _recording_plugins(recording_plan: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    plan = recording_plan if isinstance(recording_plan, Mapping) else {}
    contract = plan.get("recording_contract") if isinstance(plan.get("recording_contract"), Mapping) else {}
    descriptors = contract.get("source_descriptors") if isinstance(contract.get("source_descriptors"), Mapping) else {}
    return {
        str(key): {"version": str(descriptor["plugin_version"]), "role": "recording"}
        for key, descriptor in sorted(descriptors.items())
        if key not in INTERNAL_RECORDING_SOURCE_KEYS
        and isinstance(descriptor, Mapping)
        and descriptor.get("plugin_version")
    }


def _version(manifest: Mapping[str, Any]) -> str:
    return str(manifest.get("version") or "")


def _optional_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else None
