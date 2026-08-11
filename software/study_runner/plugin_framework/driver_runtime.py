"""Runtime used by the single ``driver.py`` entrypoint in API-v4 plugins."""
from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import threading
from typing import Any, Mapping

from .plugin_api import Plugin, PluginContext
from .process_host import PROTOCOL_PREFIX


_OUTPUT_LOCK = threading.Lock()


def run_plugin_driver(plugin_key: str) -> int:
    """Load a plugin helper inside the child and serve stdin until shutdown."""

    normalized = str(plugin_key or "").strip()
    if not normalized or not normalized.replace("_", "a").isalnum():
        print("Invalid plugin key.", file=sys.stderr, flush=True)
        return 2
    try:
        package_directory = _plugin_package_directory(normalized)
        module = importlib.import_module(f"study_runner.plugins.{package_directory}.plugin")
        plugin = getattr(module, "PLUGIN", None)
        if not isinstance(plugin, Plugin):
            raise TypeError("plugin module does not expose PLUGIN")
    except Exception as error:
        print(f"Could not load plugin '{normalized}': {error}", file=sys.stderr, flush=True)
        return 3

    context: PluginContext | None = None
    should_exit = False
    for raw_line in sys.stdin:
        line = raw_line.rstrip("\r\n")
        if line.startswith(PROTOCOL_PREFIX):
            try:
                request = json.loads(line[len(PROTOCOL_PREFIX):])
            except json.JSONDecodeError as error:
                _emit_diagnostic(f"Invalid protocol message: {error}", level="error")
                continue
            if not isinstance(request, dict) or request.get("kind") != "request":
                _emit_diagnostic("Ignored non-request protocol message.", level="warning")
                continue
            request_id = str(request.get("id") or "")
            operation = str(request.get("operation") or "")
            payload = request.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            try:
                if operation == "initialize":
                    context = _context_from_payload(payload.get("context"))
                    if plugin.initialize:
                        plugin.initialize(context)
                    result: Any = None
                elif operation == "validate_study_setting":
                    if plugin.validate_study_setting is None:
                        raise RuntimeError("plugin does not validate study settings")
                    plugin.validate_study_setting(
                        str(payload.get("field_name") or ""),
                        str(payload.get("value") or ""),
                    )
                    result = None
                else:
                    refreshed = payload.pop("_context", None)
                    if isinstance(refreshed, dict):
                        context = _context_from_payload(refreshed)
                    if context is None:
                        raise RuntimeError("plugin has not been initialized")
                    result, should_exit = _dispatch(plugin, context, operation, payload)
                _emit_response(request_id, ok=True, result=result)
            except Exception as error:
                _emit_response(request_id, ok=False, error=f"{type(error).__name__}: {error}")
            if should_exit:
                break
            continue

        if context is None:
            print("Plugin is not initialized yet.", flush=True)
            continue
        if not _handle_console_line(plugin, context, line):
            print(
                "Unknown plugin command. Type 'help' for the generic commands; "
                "the line was received unchanged.",
                flush=True,
            )
    return 0


def _plugin_package_directory(plugin_key: str) -> str:
    """Resolve a manifest key to its bundle folder without a core key map."""

    plugins_root = Path(__file__).resolve().parent.parent / "plugins"
    for directory in sorted(plugins_root.iterdir(), key=lambda item: item.name):
        manifest_path = directory / "manifest.json"
        if not directory.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and str(manifest.get("plugin_key") or "") == plugin_key:
            return directory.name
    raise LookupError(f"no plugin bundle declares key {plugin_key!r}")


def _dispatch(
    plugin: Plugin,
    context: PluginContext,
    operation: str,
    payload: Mapping[str, Any],
) -> tuple[Any, bool]:
    if operation == "status":
        return (plugin.get_status(context) if plugin.get_status else {}), False
    if operation in {"start", "stop", "restart"}:
        handler = getattr(plugin, operation, None)
        if not callable(handler):
            raise RuntimeError(f"plugin does not support {operation}")
        return handler(context), False
    if operation == "admin_action":
        if plugin.run_admin_action is None:
            raise RuntimeError("plugin does not support admin actions")
        return plugin.run_admin_action(
            context,
            str(payload.get("action") or ""),
            _dict(payload.get("payload")),
        ), False
    if operation == "participant_action":
        if plugin.run_participant_action is None:
            raise RuntimeError("plugin does not support participant actions")
        return plugin.run_participant_action(
            context,
            str(payload.get("action") or ""),
            _dict(payload.get("payload")),
        ), False
    if operation == "participant_ingest":
        if plugin.ingest_participant is None:
            raise RuntimeError("plugin does not support participant ingest")
        return plugin.ingest_participant(
            context,
            str(payload.get("ingest") or ""),
            _dict(payload.get("payload")),
        ), False
    if operation in {"trial_start", "trial_stop", "trial_marker"}:
        handler = {
            "trial_start": plugin.on_trial_start,
            "trial_stop": plugin.on_trial_stop,
            "trial_marker": plugin.on_trial_marker,
        }[operation]
        if handler is None:
            return None, False
        return handler(context, deepcopy(dict(payload))), False
    if operation == "interval_summary":
        if plugin.get_interval_summary is None:
            return {}, False
        return plugin.get_interval_summary(
            context,
            float(payload.get("start_epoch") or 0.0),
            float(payload.get("end_epoch") or 0.0),
        ), False
    if operation == "interval_export":
        if plugin.export_interval_samples is None:
            return [], False
        return plugin.export_interval_samples(
            context,
            float(payload.get("start_epoch") or 0.0),
            float(payload.get("end_epoch") or 0.0),
        ), False
    if operation == "publish":
        if plugin.publish_destination is None:
            raise RuntimeError("plugin is not an upload destination")
        return plugin.publish_destination(context, _dict(payload.get("payload"))), False
    if operation == "shutdown":
        if plugin.stop:
            try:
                plugin.stop(context)
            except Exception as error:
                _emit_diagnostic(f"Plugin stop during shutdown failed: {error}", level="warning")
        return {"stopped": True}, True
    raise RuntimeError(f"unsupported operation: {operation}")


def _handle_console_line(plugin: Plugin, context: PluginContext, line: str) -> bool:
    if plugin.handle_console_line is not None:
        result = plugin.handle_console_line(context, line)
        if result is not None:
            if isinstance(result, str):
                print(result, flush=True)
            else:
                print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        return True
    command = line.strip().lower()
    if command == "help":
        print("Generic commands: help, status, start, stop, restart", flush=True)
        return True
    if command == "status":
        result = plugin.get_status(context) if plugin.get_status else {}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
        return True
    if command in {"start", "stop", "restart"}:
        handler = getattr(plugin, command, None)
        if not callable(handler):
            print(f"Plugin does not support {command}.", flush=True)
        else:
            result = handler(context)
            if result is not None:
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
        return True
    return False


def _context_from_payload(value: Any) -> PluginContext:
    if not isinstance(value, dict):
        raise ValueError("initialize.context must be a JSON object")
    hardware_config = _dict(value.get("hardware_config"))

    def persist(updated: dict[str, Any]) -> None:
        _emit(
            {
                "kind": "persist_hardware_config",
                "hardware_config": deepcopy(updated),
            }
        )

    return PluginContext(
        base_dir=Path(str(value.get("base_dir") or ".")).resolve(),
        data_dir=Path(str(value.get("data_dir") or ".")).resolve(),
        hardware_config=hardware_config,
        local_secrets=_dict(value.get("local_secrets")),
        local_secrets_file=Path(str(value.get("local_secrets_file") or ".")).resolve(),
        runtime_locked=bool(value.get("runtime_locked", False)),
        persist_hardware_config=persist,
    )


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _emit_response(
    request_id: str,
    *,
    ok: bool,
    result: Any = None,
    error: str | None = None,
) -> None:
    payload: dict[str, Any] = {"kind": "response", "id": request_id, "ok": bool(ok)}
    if ok:
        payload["result"] = result
    else:
        payload["error"] = str(error or "plugin operation failed")
    _emit(payload)


def _emit_diagnostic(message: str, *, level: str) -> None:
    _emit({"kind": "diagnostic", "level": level, "message": str(message)})


def _emit(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    with _OUTPUT_LOCK:
        print(PROTOCOL_PREFIX + encoded, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: driver_runtime.py <plugin-key>", file=sys.stderr)
        return 2
    return run_plugin_driver(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
