"""Runtime used by the single ``driver.py`` entrypoint in API-v4 plugins."""
from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import importlib
import json
from pathlib import Path
import sys
import threading
from typing import Any, Mapping

from study_runner.contracts.plugin_api import Plugin, PluginContext
from study_runner.contracts.card_validation_primitives import CardValidationError
from .plugin_layout import resolve_plugin
from .plugin_secrets import resolve_plugin_secret
from .process_host import PROTOCOL_PREFIX


_OUTPUT_LOCK = threading.Lock()
_PROTOCOL_OUTPUT = None

# Operations that can take minutes (an upload) run beside the request loop, so
# status polls and admin actions are still answered while they work. One at a
# time: the host sends the next only after this one answered or timed out, and
# a timed-out one ends with the whole process (process_host.py).
BACKGROUND_OPERATIONS = frozenset({"publish"})
_background_busy = threading.Event()


def run_plugin_driver(plugin_key: str) -> int:
    """Keep plugin/thread prints off the machine protocol's stdout pipe."""
    global _PROTOCOL_OUTPUT
    previous = _PROTOCOL_OUTPUT
    _PROTOCOL_OUTPUT = sys.stdout
    try:
        with redirect_stdout(sys.stderr):
            return _serve_plugin_driver(plugin_key)
    finally:
        _PROTOCOL_OUTPUT = previous


def _serve_plugin_driver(plugin_key: str) -> int:
    """Load a plugin helper inside the child and serve stdin until shutdown."""

    normalized = str(plugin_key or "").strip()
    if not normalized or not normalized.replace("_", "a").isalnum():
        print("Invalid plugin key.", file=sys.stderr, flush=True)
        return 2
    try:
        package_directory = _plugin_package_directory(normalized)
        module = importlib.import_module(f"{package_directory}.plugin")
        plugin = getattr(module, "PLUGIN", None)
        if not isinstance(plugin, Plugin):
            raise TypeError("plugin module does not expose PLUGIN")
        directory = resolve_plugin(normalized)[0]
        # The raw manifest.json, not the normalized catalog shape: a child
        # process reads its own file directly rather than importing the
        # discovery machinery. Here "capabilities" is still the authored
        # {name: config} dict; process_host.py (host side, normalized
        # manifest) and card_catalog.py (capability_config, a different key
        # entirely) each read a different shape for the same question -- see
        # the comment on PluginProcessRuntime.is_card.
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        # A manifest can write "capabilities" as a plain list of names with
        # no settings (e.g. `["health"]`, as packaging_probe's manifest
        # does) instead of an {name: config} object -- both are valid. Turn
        # a list into an empty-config dict here so the .get() calls below
        # work either way.
        raw_capabilities = manifest.get("capabilities") or {}
        if isinstance(raw_capabilities, list):
            raw_capabilities = {name: {} for name in raw_capabilities}
        card_contract = raw_capabilities.get("card_contract")
        if card_contract:
            for name in ("get_card_defaults", "normalize_card_config"):
                if not callable(getattr(plugin, name, None)):
                    raise TypeError(f"Card extension is missing {name}")
            if set(card_contract["question_types"]) - set(card_contract.get("answerless_types", [])):
                if not callable(plugin.validate_card_answer):
                    raise TypeError("Answerable card extension is missing validate_card_answer")
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
            if operation in {"initialize", "start", "stop", "restart", "admin_action", "shutdown"}:
                _emit_diagnostic(f"Control request {request_id}: {operation}", level="info")
            payload = request.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            try:
                if operation in {"card_defaults", "card_normalize", "card_validate_answer"}:
                    question_type = payload.get("question_type")
                    if not card_contract or question_type not in card_contract["question_types"]:
                        raise RuntimeError("Card operation requested an undeclared question type")
                    result = _dispatch_card(plugin, operation, payload)
                    # Reject non-JSON data before the generic serializer can coerce it.
                    json.dumps(result, allow_nan=False)
                    if operation != "card_validate_answer" and (
                        not isinstance(result, dict) or result.get("type") != question_type
                    ):
                        raise RuntimeError("Card returned an invalid configuration object")
                elif operation == "shutdown" and card_contract:
                    result, should_exit = None, True
                elif operation == "initialize":
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
                    if operation in BACKGROUND_OPERATIONS:
                        _start_background_operation(plugin, context, operation, payload, request_id)
                        continue
                    result, should_exit = _dispatch(plugin, context, operation, payload)
                _emit_response(request_id, ok=True, result=result)
            except Exception as error:
                invalid_input = isinstance(error, CardValidationError) or (
                    operation == "validate_study_setting" and isinstance(error, ValueError)
                )
                _emit_response(
                    request_id,
                    ok=False,
                    error=str(error) if invalid_input else f"{type(error).__name__}: {error}",
                    error_kind="invalid_input" if invalid_input else "extension_failure",
                )
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

    return resolve_plugin(plugin_key)[1]


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


def _dispatch_card(plugin: Plugin, operation: str, payload: Mapping[str, Any]) -> Any:
    question_type = payload["question_type"]
    if operation == "card_defaults":
        return plugin.get_card_defaults(question_type)
    if operation == "card_normalize":
        data, host_data, index = payload.get("question_data"), payload.get("host_data"), payload.get("question_index")
        if not isinstance(data, dict) or not isinstance(host_data, dict) or type(index) is not int or index < 1:
            raise RuntimeError("Malformed card normalization request")
        return plugin.normalize_card_config(question_type, data, index, host_data)
    question, number = payload.get("question"), payload.get("question_number")
    if not isinstance(question, dict) or type(number) is not int or number < 1 or "answer" not in payload:
        raise RuntimeError("Malformed card answer request")
    if plugin.validate_card_answer is None:
        raise RuntimeError("This card has no answer validator")
    return plugin.validate_card_answer(question_type, question, payload["answer"], number)


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
        persist_hardware_config=persist if value.get("can_persist_hardware_config", True) else None,
        secret_resolver=resolve_plugin_secret,
    )


def _dict(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _start_background_operation(
    plugin: Plugin,
    context: PluginContext,
    operation: str,
    payload: dict[str, Any],
    request_id: str,
) -> None:
    if _background_busy.is_set():
        raise RuntimeError(f"{operation} is already running in this plugin")
    _background_busy.set()

    def run() -> None:
        try:
            result, _ = _dispatch(plugin, context, operation, payload)
            _emit_response(request_id, ok=True, result=result)
        except Exception as error:
            _emit_response(request_id, ok=False, error=f"{type(error).__name__}: {error}")
        finally:
            _background_busy.clear()

    threading.Thread(target=run, name=f"plugin-{operation}", daemon=True).start()


def _emit_response(
    request_id: str,
    *,
    ok: bool,
    result: Any = None,
    error: str | None = None,
    error_kind: str = "extension_failure",
) -> None:
    payload: dict[str, Any] = {"kind": "response", "id": request_id, "ok": bool(ok)}
    if ok:
        payload["result"] = result
    else:
        payload["error"] = str(error or "plugin operation failed")
        payload["error_kind"] = error_kind
    _emit(payload)


def _emit_diagnostic(message: str, *, level: str) -> None:
    _emit({"kind": "diagnostic", "level": level, "message": str(message)})


def _emit(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    with _OUTPUT_LOCK:
        stream = _PROTOCOL_OUTPUT if _PROTOCOL_OUTPUT is not None else sys.stdout
        stream.write(PROTOCOL_PREFIX + encoded + "\n")
        stream.flush()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: driver_runtime.py <plugin-key>", file=sys.stderr)
        return 2
    return run_plugin_driver(args[0])


if __name__ == "__main__":
    raise SystemExit(main())

