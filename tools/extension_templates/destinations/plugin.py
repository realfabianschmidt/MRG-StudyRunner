"""Example upload-destination extension -- Study Runner extension SDK template.

Rename `example_destination` everywhere (this file's PLUGIN.key/config_key
and the manifest's plugin_key/config_key) before shipping;
`tools/extension_sdk.py new destinations <your_key>` does this substitution
for you.

`publish_destination` is the one handler `upload_destination` requires. A
real destination uploads the artifacts named in `payload` to a remote
location and reports the outcome; this example uploads nothing and always
reports success, to prove the contract shape without any network code.
See `study_runner/extensions/destinations/nextcloud_upload/plugin.py` for a
complete, real implementation once you outgrow this template.
"""
from __future__ import annotations

from typing import Any

from study_runner.contracts.plugin_api import Plugin, PluginContext


def _initialize(context: PluginContext) -> None:
    del context  # unused in this minimal example


def _status(context: PluginContext) -> dict[str, Any]:
    del context
    return {"status": "ok"}


def _publish(context: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
    del context
    return {"ok": True, "detail": f"example destination received fields: {sorted(payload)}"}


PLUGIN = Plugin(
    key="example_destination",
    label="Example destination",
    category="storage",
    config_key="example_destination",
    initialize=_initialize,
    get_status=_status,
    publish_destination=_publish,
)
