"""Hardware settings and generic plugin runtime endpoints."""
import json

from flask import Blueprint, current_app, jsonify, request

from study_runner.plugin_framework.registry import (
    apply_enabled_runtime,
    get_plugin,
    get_plugin_status,
    initialize_plugin,
    run_runtime_action,
)
from study_runner.runtime_core.settings.hardware_settings_service import (
    HardwareRevisionConflict,
    hardware_config_revision,
    load_hardware_config,
    update_hardware_config as update_hardware_config_transaction,
)
from study_runner.runtime_core.settings.secrets_service import redact_hardware_config, update_local_secrets
from study_runner.runtime_core.settings.plugin_settings_service import (
    PluginSettingsError,
    apply_plugin_settings,
    build_plugin_settings_schema,
)
from study_runner.data_core.host.study_sensor_runtime import SESSION_OVERRIDE_KEYS, STUDY_SENSOR_KEYS
from .helpers import (
    _apply_session_override_runtime,
    _copy_config,
    _hardware_disabled,
    _plugin_context,
    _rebuild_active_study_runtime_config,
    _refresh_trial_runtime,
    _save_hardware_secret_payload,
    _sensor_runtime_state,
    _session_overrides,
    _set_runtime_enabled,
    _set_session_override,
)

bp = Blueprint("sensors", __name__)


@bp.route("/api/hardware-config")
def get_hardware_config():
    hardware_config = current_app.config.get("HARDWARE_CONFIG", {})
    payload = redact_hardware_config(
        hardware_config,
        current_app.config.get("LOCAL_SECRETS", {}),
    )
    payload["_revision"] = hardware_config_revision(hardware_config)
    return jsonify(payload)


@bp.route("/api/admin/plugin-settings", methods=["GET"])
def get_plugin_settings():
    """Schema plus the values actually in force right now.

    The effective-value rule (disk wins, manifest default only fills a missing
    key) lives here on the backend so the UI never has to merge the two.
    """
    return jsonify({
        "ok": True,
        "plugins": build_plugin_settings_schema(current_app.config.get("HARDWARE_CONFIG", {})),
        "revision": hardware_config_revision(current_app.config.get("HARDWARE_CONFIG", {})),
    })


@bp.route("/api/admin/plugin-settings/<plugin_key>", methods=["POST"])
def update_plugin_settings(plugin_key):
    """Write only the named settings, deep-merged into the existing config.

    Deliberately not the whole-document POST /api/hardware-config: a per-panel
    save through that route would wipe every sibling key it did not know about.
    """
    payload = request.get_json(silent=True) or {}
    updates = payload.get("settings")
    if not isinstance(updates, dict) or not updates:
        return jsonify({"ok": False, "error": "settings must be a non-empty object."}), 400

    try:
        def apply_settings(current_config):
            updated, restart = apply_plugin_settings(
                current_config,
                plugin_key,
                updates,
            )
            current_config.clear()
            current_config.update(updated)
            return restart

        updated_config, restart_required, revision = update_hardware_config_transaction(
            current_app.config["HARDWARE_CONFIG_FILE"],
            apply_settings,
            expected_revision=(
                str(payload.get("revision"))
                if payload.get("revision") is not None
                else None
            ),
        )
    except PluginSettingsError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HardwareRevisionConflict as error:
        return jsonify({"ok": False, "error": str(error), "code": "hardware_revision_conflict"}), 409

    current_app.config["HARDWARE_CONFIG"] = updated_config
    _refresh_trial_runtime()

    # initialize_plugin() from a route bypasses the startup hardware guard, so
    # a settings save must not be able to start real devices during tests.
    if not restart_required and not _hardware_disabled():
        try:
            initialize_plugin(plugin_key, _plugin_context())
        except Exception as error:
            print(f"[SETTINGS] Could not re-initialize {plugin_key}: {error}")

    return jsonify({
        "ok": True,
        "restart_required": restart_required,
        "revision": revision,
        "plugins": build_plugin_settings_schema(updated_config),
    })


@bp.route("/api/hardware-config", methods=["POST"])
def update_hardware_config():
    config_data = request.get_json()
    if not isinstance(config_data, dict):
        return jsonify({"ok": False, "error": "hardware_config payload must be a JSON object."}), 400

    incoming = _copy_config(config_data)
    expected_revision = incoming.pop("_revision", None)
    try:
        def merge_update(current_config):
            sanitized, secret_updated = _save_hardware_secret_payload(incoming)
            merged = _merge_hardware_config_preserving_unknown(current_config, sanitized)
            current_config.clear()
            current_config.update(merged)
            return secret_updated

        sanitized_config, _secret_updated, revision = update_hardware_config_transaction(
            current_app.config["HARDWARE_CONFIG_FILE"],
            merge_update,
            expected_revision=(
                str(expected_revision) if expected_revision is not None else None
            ),
        )
    except HardwareRevisionConflict as error:
        current = load_hardware_config(current_app.config["HARDWARE_CONFIG_FILE"])
        return jsonify(
            {
                "ok": False,
                "error": str(error),
                "code": "hardware_revision_conflict",
                "current_revision": hardware_config_revision(current),
            }
        ), 409
    current_app.config["HARDWARE_CONFIG"] = sanitized_config
    _refresh_trial_runtime()

    return jsonify(
        {
            "ok": True,
            "revision": revision,
            "restart_required": True,
            "message": (
                "Hardware config saved. Secrets stay backend-local. "
                "Restart Study Runner to load the new plugin configuration."
            ),
        }
    )


def _merge_hardware_config_preserving_unknown(previous: dict, incoming: dict) -> dict:
    """Deep-merge browser edits so absent/opaque plugin sections survive.

    The public hardware payload intentionally replaces a removed plugin's
    section with a ``settings_hidden`` placeholder.  Treating that placeholder
    as data would destroy the real settings on the next save.
    """

    merged = json.loads(json.dumps(previous if isinstance(previous, dict) else {}))

    def merge_mapping(target: dict, updates: dict) -> None:
        for key, value in updates.items():
            if isinstance(value, dict) and value.get("settings_hidden") is True:
                continue
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge_mapping(target[key], value)
            else:
                target[key] = json.loads(json.dumps(value))

    merge_mapping(merged, incoming)
    return merged


@bp.route("/api/admin/plugins/<plugin_key>/config", methods=["DELETE"])
def remove_stored_plugin_config(plugin_key: str):
    """Erase a removed plugin's leftover ``settings_hidden`` placeholder.

    _merge_hardware_config_preserving_unknown() (above) is what keeps that
    placeholder alive across every other save -- by design, so re-installing
    the plugin does not lose its settings. This is the one explicit,
    confirmed action that actually discards it, for an operator who is sure
    the plugin is gone for good. Only applies to plugins that are not
    currently installed; toggling or clearing a live plugin's settings goes
    through the normal save/enabled routes instead.
    """

    if get_plugin(plugin_key) is not None:
        return jsonify(
            {
                "ok": False,
                "error": f"'{plugin_key}' is currently installed; this action is only for a removed plugin's leftover configuration.",
            }
        ), 400

    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") is not True:
        return jsonify({"ok": False, "error": "Explicit confirmation is required."}), 400

    def remove_section(hardware_config: dict) -> bool:
        return hardware_config.pop(plugin_key, None) is not None

    try:
        hardware_config, removed, revision = update_hardware_config_transaction(
            current_app.config["HARDWARE_CONFIG_FILE"],
            remove_section,
        )
    except HardwareRevisionConflict as error:
        return jsonify({"ok": False, "error": str(error), "code": "hardware_revision_conflict"}), 409

    current_app.config["HARDWARE_CONFIG"] = hardware_config

    # secret_fields() only knows currently installed plugins, so it cannot
    # name this plugin's secret field once it is removed -- local_secrets.json
    # is keyed by plugin_key regardless of field name, so drop the whole
    # section rather than trying to look one up.
    def remove_secret(local_secrets: dict) -> bool:
        return local_secrets.pop(plugin_key, None) is not None

    local_secrets, secret_removed = update_local_secrets(
        current_app.config["LOCAL_SECRETS_FILE"],
        remove_secret,
    )
    current_app.config["LOCAL_SECRETS"] = local_secrets

    return jsonify(
        {
            "ok": True,
            "plugin": plugin_key,
            "removed": bool(removed),
            "secret_removed": secret_removed,
            "revision": revision,
        }
    )


@bp.route("/api/admin/plugins/<plugin_key>/enabled", methods=["POST"])
def update_plugin_enabled(plugin_key: str):
    payload = request.get_json() or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "error": "enabled must be true or false."}), 400

    if plugin_key in SESSION_OVERRIDE_KEYS:
        try:
            _set_session_override(plugin_key, enabled)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        runtime_result = _apply_session_override_runtime(plugin_key, enabled)
        return jsonify(
            {
                "ok": True,
                "plugin": plugin_key,
                "enabled": enabled,
                "restart_required": False,
                "active_runtime_updated": isinstance(current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG"), dict),
                "study_controlled": False,
                "temporary_override": True,
                "session_overrides": _session_overrides(),
                "sensor_runtime": _sensor_runtime_state(),
                "runtime": runtime_result,
                "runtime_status": get_plugin_status(plugin_key, _plugin_context()),
            }
        )

    active_config = current_app.config.get("ACTIVE_STUDY_HARDWARE_CONFIG")
    try:
        def apply_toggle(hardware_config):
            _set_runtime_enabled(hardware_config, plugin_key, enabled)

        hardware_config, _, revision = update_hardware_config_transaction(
            current_app.config["HARDWARE_CONFIG_FILE"],
            apply_toggle,
            expected_revision=(
                str(payload.get("revision"))
                if payload.get("revision") is not None
                else None
            ),
        )
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except HardwareRevisionConflict as error:
        return jsonify({"ok": False, "error": str(error), "code": "hardware_revision_conflict"}), 409

    current_app.config["HARDWARE_CONFIG"] = hardware_config
    active_runtime_updated = False
    study_controlled = bool(active_config) and plugin_key in STUDY_SENSOR_KEYS
    _refresh_trial_runtime()

    try:
        if not study_controlled:
            apply_enabled_runtime(plugin_key, enabled, _plugin_context())
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400

    return jsonify(
        {
            "ok": True,
            "plugin": plugin_key,
            "enabled": enabled,
            "revision": revision,
            "restart_required": False,
            "active_runtime_updated": active_runtime_updated,
            "study_controlled": study_controlled,
            "runtime_status": get_plugin_status(plugin_key, _plugin_context()),
        }
    )


def _run_plugin_action_json(plugin_key: str, action: str):
    if get_plugin(plugin_key) is None:
        return jsonify({"ok": False, "error": f"Plugin '{plugin_key}' is not installed."}), 404
    try:
        normalized_action = str(action or "").strip().lower()
        if plugin_key in STUDY_SENSOR_KEYS and normalized_action in {"start", "stop", "restart"}:
            _set_session_override(plugin_key, normalized_action != "stop")
            _rebuild_active_study_runtime_config()
        context = _plugin_context()
        if (
            plugin_key in STUDY_SENSOR_KEYS
            and normalized_action in {"start", "restart"}
            and not _hardware_disabled()
        ):
            # A sensor switched on against the loaded study was set up while
            # disabled; initialize it with the new configuration first. For an
            # unchanged configuration this is a no-op (process_host guard).
            initialize_plugin(plugin_key, context)
        coordinator = current_app.config.get("SENSOR_COORDINATOR")
        if coordinator:
            result = coordinator.run_action(plugin_key, action, context)
        else:
            result = run_runtime_action(plugin_key, action, context)
        result["temporary_override"] = plugin_key in STUDY_SENSOR_KEYS
        result["sensor_runtime"] = _sensor_runtime_state()
        return jsonify(result)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@bp.route("/api/admin/plugins/<plugin_key>/<action>", methods=["POST"])
def run_plugin_runtime_action(plugin_key: str, action: str):
    return _run_plugin_action_json(plugin_key, action)
