"""Supervise API-v4 plugin drivers and expose their line-oriented console.

The visible terminal deliberately stays simple: drivers receive UTF-8 lines on
stdin and may print arbitrary text on stdout/stderr.  Framework RPC uses a
reserved prefix, so high-rate scientific samples never share the terminal data
path; sensor drivers publish those directly through their declared transport.
"""
from __future__ import annotations

import atexit
from collections import deque
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Mapping

from .plugin_api import Plugin, PluginContext


PROTOCOL_PREFIX = "@study-runner "
PROTOCOL_NAME = "study-runner-stdio/v1"
MAX_CONSOLE_LINE_BYTES = 16 * 1024
MAX_OUTPUT_LINE_CHARS = 64 * 1024
OUTPUT_RING_LINES = 5_000
LOG_ROTATE_BYTES = 10 * 1024 * 1024
LOG_ROTATE_GENERATIONS = 3
MAX_RESTARTS = 3
STARTUP_TIMEOUT_MS = 5_000


class PluginProcessError(RuntimeError):
    """The plugin process could not complete a framework operation."""


class ConsoleLockedError(PermissionError):
    """stdin is locked while a study is running."""


class _PendingResponse:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: dict[str, Any] | None = None


class PluginProcessRuntime:
    def __init__(self, manifest: Mapping[str, Any], directory: Path) -> None:
        self.manifest = deepcopy(dict(manifest))
        self.key = str(manifest["plugin_key"])
        self.directory = Path(directory).resolve()
        self.runtime_config = dict(manifest.get("runtime") or {})
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._transcript_lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[str, _PendingResponse] = {}
        self._output: deque[dict[str, Any]] = deque(maxlen=OUTPUT_RING_LINES)
        self._output_sequence = 0
        self._context: PluginContext | None = None
        self._desired_running = False
        self._restart_count = 0
        self._last_exit_code: int | None = None
        self._last_exit_at: float | None = None
        self._unlocked_until = 0.0
        self._unlock_run_id: str | None = None
        self._log_path: Path | None = None
        self._intervention_path: Path | None = None
        self._last_health_event_at: float | None = None
        self._last_sample_event_at: float | None = None
        self._completed_operations: set[str] = set()

    # -- process lifecycle -------------------------------------------------

    def initialize(self, context: PluginContext) -> None:
        self._context = context
        self._desired_running = True
        self._ensure_started()
        initialize_timeout = (
            ((self.manifest.get("runtime") or {}).get("operation_timeouts_ms") or {}).get(
                "initialize"
            )
            or self.manifest.get("request_timeout_ms")
            or 0
        )
        self.request(
            "initialize",
            {"context": _serialize_context(context)},
            timeout_ms=max(
                STARTUP_TIMEOUT_MS,
                int(initialize_timeout),
            ),
        )

    def request(
        self,
        operation: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
        _start_if_needed: bool = True,
    ) -> Any:
        if _start_if_needed:
            self._ensure_started()
        else:
            with self._lock:
                process = self._process
            if process is None or process.poll() is not None:
                raise PluginProcessError(f"Plugin '{self.key}' is not running.")
        request_id = uuid.uuid4().hex
        pending = _PendingResponse()
        with self._lock:
            self._pending[request_id] = pending
            first_operation = operation not in self._completed_operations
        request_payload = deepcopy(dict(payload or {}))
        # Machine settings and study-scoped secrets may change while a driver
        # remains alive. Refresh the child context on each framework request
        # without re-running its stateful initialize hook.
        with self._lock:
            context = self._context
        if context is not None:
            request_payload["_context"] = _serialize_context(context)
        envelope = {
            "kind": "request",
            "id": request_id,
            "operation": str(operation),
            "payload": request_payload,
        }
        try:
            self._write_protocol(envelope)
            configured_timeout_ms = int(
                timeout_ms
                if timeout_ms is not None
                else (
                    ((self.manifest.get("runtime") or {}).get("operation_timeouts_ms") or {}).get(
                        operation
                    )
                    or self.manifest.get("request_timeout_ms")
                    or 1_000
                )
            )
            if timeout_ms is None and first_operation:
                configured_timeout_ms = max(STARTUP_TIMEOUT_MS, configured_timeout_ms)
            timeout = max(0.05, float(configured_timeout_ms) / 1_000.0)
            if not pending.event.wait(timeout):
                raise PluginProcessError(
                    f"Plugin '{self.key}' timed out during {operation} after {timeout:.3f}s."
                )
            response = pending.payload or {}
            if response.get("ok") is not True:
                raise PluginProcessError(
                    str(response.get("error") or f"Plugin '{self.key}' failed during {operation}.")
                )
            with self._lock:
                self._completed_operations.add(operation)
            return response.get("result")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def shutdown(self) -> None:
        self.expire_console_unlock()
        with self._lock:
            process = self._process
            self._desired_running = False
        if process is None:
            return
        try:
            # Respect the same per-operation contract as every other RPC.
            # Hardware drivers may need several seconds to stop BLE/serial
            # streams cleanly; killing them after one second can orphan the
            # device process and lose its final QC/log flush.
            self.request("shutdown", _start_if_needed=False)
        except Exception:
            pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        self._close_process_streams(process)
        with self._lock:
            if self._process is process:
                self._process = None

    def _ensure_started(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            command = self._command()
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            software_root = str(self.directory.parents[2])
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                item for item in (software_root, existing_pythonpath) if item
            )
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(self.directory),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        if os.name == "nt"
                        else 0
                    ),
                )
            except OSError as error:
                raise PluginProcessError(
                    f"Could not start plugin driver '{self.key}': {error}"
                ) from error
            self._process = process
            self._desired_running = True
            self._last_exit_code = None
            self._completed_operations.clear()
            self._configure_log_path()
            self._append_output("system", f"Started plugin driver (pid {process.pid}).")
            threading.Thread(
                target=self._read_stream,
                args=(process, "stdout", process.stdout),
                name=f"plugin-{self.key}-stdout",
                daemon=True,
            ).start()
            threading.Thread(
                target=self._read_stream,
                args=(process, "stderr", process.stderr),
                name=f"plugin-{self.key}-stderr",
                daemon=True,
            ).start()
            threading.Thread(
                target=self._wait_for_exit,
                args=(process,),
                name=f"plugin-{self.key}-wait",
                daemon=True,
            ).start()

    def _command(self) -> list[str]:
        entrypoint = str(self.runtime_config.get("entrypoint") or "driver.py")
        driver_path = (self.directory / entrypoint).resolve()
        if not driver_path.is_relative_to(self.directory) or not driver_path.is_file():
            raise PluginProcessError(
                f"Plugin '{self.key}' driver entrypoint is missing: {entrypoint}"
            )
        if bool(getattr(sys, "frozen", False)):
            return [sys.executable, "--plugin-driver", self.key]
        return [sys.executable, "-u", str(driver_path)]

    def _read_stream(self, process: subprocess.Popen[str], source: str, stream: Any) -> None:
        if stream is None:
            return
        try:
            for raw_line in iter(stream.readline, ""):
                line = raw_line.rstrip("\r\n")
                if source == "stdout" and line.startswith(PROTOCOL_PREFIX):
                    if self._handle_protocol_line(line[len(PROTOCOL_PREFIX):]):
                        continue
                self._append_output(source, line)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _handle_protocol_line(self, raw_json: str) -> bool:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        kind = str(payload.get("kind") or "")
        if kind == "response":
            request_id = str(payload.get("id") or "")
            with self._lock:
                pending = self._pending.get(request_id)
            if pending is not None:
                pending.payload = payload
                pending.event.set()
            return True
        if kind == "persist_hardware_config":
            config = payload.get("hardware_config")
            context = self._context
            if isinstance(config, dict) and context and context.persist_hardware_config:
                try:
                    context.persist_hardware_config(deepcopy(config))
                except Exception as error:
                    self._append_output("system", f"Could not persist plugin settings: {error}")
            return True
        if kind == "diagnostic":
            self._append_output("diagnostic", str(payload.get("message") or ""), payload)
            return True
        if kind in {"health", "sample"}:
            now = time.time()
            with self._lock:
                if kind == "health":
                    self._last_health_event_at = now
                else:
                    self._last_sample_event_at = now
            self._append_output(kind, str(payload.get("message") or kind), payload)
            return True
        return True

    def _wait_for_exit(self, process: subprocess.Popen[str]) -> None:
        exit_code = process.wait()
        self._close_process_streams(process)
        restart = False
        with self._lock:
            if self._process is not process:
                return
            self._process = None
            self._last_exit_code = int(exit_code)
            self._last_exit_at = time.time()
            for pending in self._pending.values():
                pending.payload = {
                    "kind": "response",
                    "ok": False,
                    "error": f"Plugin process exited with code {exit_code}.",
                }
                pending.event.set()
            restart = self._desired_running and self._restart_count < MAX_RESTARTS
            if restart:
                self._restart_count += 1
        self._append_output("system", f"Plugin driver exited with code {exit_code}.")
        if restart:
            threading.Thread(
                target=self._restart_after_exit,
                name=f"plugin-{self.key}-restart",
                daemon=True,
            ).start()

    @staticmethod
    def _close_process_streams(process: subprocess.Popen[str]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def _restart_after_exit(self) -> None:
        while True:
            with self._lock:
                attempt = self._restart_count
            delay = min(4.0, 0.5 * (2 ** max(0, attempt - 1)))
            time.sleep(delay)
            with self._lock:
                if not self._desired_running or self._process is not None:
                    return
                context = self._context
            try:
                self._ensure_started()
                if context is not None:
                    initialize_timeout = (
                        ((self.manifest.get("runtime") or {}).get("operation_timeouts_ms") or {}).get(
                            "initialize"
                        )
                        or self.manifest.get("request_timeout_ms")
                        or 0
                    )
                    self.request(
                        "initialize",
                        {"context": _serialize_context(context)},
                        timeout_ms=max(STARTUP_TIMEOUT_MS, int(initialize_timeout)),
                    )
                self._append_output(
                    "system",
                    f"Automatic restart {attempt}/{MAX_RESTARTS} succeeded.",
                )
                return
            except Exception as error:
                self._append_output(
                    "system",
                    f"Automatic restart {attempt}/{MAX_RESTARTS} failed: {error}",
                )
                with self._lock:
                    process = self._process
                if process is not None and process.poll() is None:
                    # An alive but uninitialised child cannot serve requests.
                    # Its waiter owns accounting and schedules the next attempt.
                    process.terminate()
                    return
                with self._lock:
                    if not self._desired_running or self._restart_count >= MAX_RESTARTS:
                        return
                    # Popen failed, so no waiter exists to account for another
                    # bounded attempt. Do that here and continue the loop.
                    self._restart_count += 1

    # -- line-oriented operator console -----------------------------------

    def write_console_line(
        self,
        line: str,
        *,
        study_running: bool,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(line, str):
            raise ValueError("console input line must be a string")
        if "\x00" in line:
            raise ValueError("console input must not contain NUL characters")
        if line.startswith(PROTOCOL_PREFIX):
            raise ValueError("console input must not use the reserved framework prefix")
        if len(line.encode("utf-8")) > MAX_CONSOLE_LINE_BYTES:
            raise ValueError(f"console input exceeds {MAX_CONSOLE_LINE_BYTES} bytes")
        if "\r" in line or "\n" in line:
            raise ValueError("console input must contain exactly one line")
        if study_running and not self.console_unlocked_for(run_id):
            raise ConsoleLockedError("Plugin stdin is locked while a study is running.")
        self._ensure_started()
        self._append_output("stdin", line)
        self._write_raw(line + "\n")
        return {"ok": True, "plugin_key": self.key, "bytes": len(line.encode("utf-8"))}

    def unlock_console(self, duration_seconds: int = 600, *, run_id: str | None = None) -> float:
        self.expire_console_unlock()
        duration = max(1, min(600, int(duration_seconds)))
        with self._lock:
            self._unlocked_until = time.time() + duration
            self._unlock_run_id = str(run_id or "").strip() or None
            return self._unlocked_until

    def expire_console_unlock(self, run_id: str | None = None) -> bool:
        """End a grant, optionally only when it belongs to ``run_id``."""

        normalized = str(run_id or "").strip() or None
        with self._lock:
            if normalized is not None and self._unlock_run_id != normalized:
                return False
            active = self._unlocked_until > 0 or self._intervention_path is not None
            if not active:
                return False
            self._append_private_transcript(
                {
                    "kind": "operator_intervention_end",
                    "plugin_key": self.key,
                    "run_id": self._unlock_run_id,
                    "ended_at_epoch": time.time(),
                }
            )
            self._unlocked_until = 0.0
            self._unlock_run_id = None
            self._intervention_path = None
        return True

    def begin_intervention_transcript(
        self,
        path: Path,
        *,
        run_id: str,
        reason: str,
    ) -> None:
        transcript_path = Path(path)
        entry = {
            "kind": "operator_intervention",
            "plugin_key": self.key,
            "run_id": str(run_id),
            "reason": str(reason),
            "started_at_epoch": time.time(),
        }
        # Fail closed before stdin is unlocked. A study-time intervention is
        # only permitted when its private audit trail has reached durable
        # storage.
        self._write_private_transcript_entry(transcript_path, entry)
        with self._lock:
            self._intervention_path = transcript_path

    @property
    def console_unlocked(self) -> bool:
        with self._lock:
            unlocked_until = self._unlocked_until
        if unlocked_until and unlocked_until <= time.time():
            self.expire_console_unlock()
            return False
        return unlocked_until > time.time()

    def console_unlocked_for(self, run_id: str | None) -> bool:
        """Whether the temporary grant belongs to this immutable study run."""

        normalized = str(run_id or "").strip() or None
        with self._lock:
            unlocked_until = self._unlocked_until
        if unlocked_until and unlocked_until <= time.time():
            self.expire_console_unlock()
            return False
        with self._lock:
            return self._unlock_run_id == normalized

    def snapshot(self, *, tail: int = 250) -> dict[str, Any]:
        unlocked = self.console_unlocked
        with self._lock:
            process = self._process
            lines = list(self._output)[-max(0, min(int(tail), 1_000)):]
            return {
                "ok": True,
                "plugin_key": self.key,
                "protocol": PROTOCOL_NAME,
                "interactive_stdin": bool(self.runtime_config.get("interactive_stdin", False)),
                "running": bool(process is not None and process.poll() is None),
                "pid": process.pid if process is not None and process.poll() is None else None,
                "last_exit_code": self._last_exit_code,
                "last_exit_at": self._last_exit_at,
                "console_unlocked": unlocked,
                "unlock_run_id": self._unlock_run_id,
                "unlocked_until_epoch": self._unlocked_until or None,
                "last_health_event_at": self._last_health_event_at,
                "last_sample_event_at": self._last_sample_event_at,
                "last_sequence": self._output_sequence,
                "lines": deepcopy(lines),
            }

    def wait_for_output(self, after_sequence: int, timeout: float = 15.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._output_sequence <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)
            return [deepcopy(item) for item in self._output if int(item["sequence"]) > after_sequence]

    # -- I/O helpers -------------------------------------------------------

    def _write_protocol(self, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        self._write_raw(PROTOCOL_PREFIX + encoded + "\n")

    def _write_raw(self, value: str) -> None:
        with self._write_lock:
            with self._lock:
                process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise PluginProcessError(f"Plugin '{self.key}' is not running.")
            try:
                process.stdin.write(value)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise PluginProcessError(f"Could not write to plugin '{self.key}': {error}") from error

    def _append_output(
        self,
        source: str,
        line: str,
        structured: Mapping[str, Any] | None = None,
    ) -> None:
        normalized = str(line)
        truncated = len(normalized) > MAX_OUTPUT_LINE_CHARS
        if truncated:
            normalized = normalized[:MAX_OUTPUT_LINE_CHARS] + " ... [truncated]"
        entry = {
            "sequence": 0,
            "timestamp_epoch": time.time(),
            "source": source,
            "line": normalized,
            "truncated": truncated,
        }
        if structured:
            entry["structured"] = deepcopy(dict(structured))
        with self._condition:
            self._output_sequence += 1
            entry["sequence"] = self._output_sequence
            self._output.append(entry)
            self._condition.notify_all()
        self._append_log_entry(entry)
        if self.console_unlocked:
            self._append_private_transcript(entry)

    def _configure_log_path(self) -> None:
        context = self._context
        root = Path(context.data_dir) if context is not None else self.directory
        self._log_path = root / "runtime" / "plugin_logs" / f"{self.key}.log"

    def _append_log_entry(self, entry: Mapping[str, Any]) -> None:
        path = self._log_path
        if path is None:
            return
        try:
            with self._log_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_log(path)
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(entry["timestamp_epoch"])))
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{stamp} [{entry['source']}] {entry['line']}\n")
        except OSError:
            # Console output remains available in memory.  A log write must not
            # block a sensor callback or kill the supervised driver.
            return

    def _append_private_transcript(self, entry: Mapping[str, Any]) -> None:
        try:
            with self._lock:
                path = self._intervention_path
                if path is None:
                    return
                # Keep the path stable until the line has reached disk; session
                # completion can then append its end record and clear the path
                # without a late stdout line appearing after that end marker.
                self._write_private_transcript_entry(path, entry)
        except OSError:
            return

    def _write_private_transcript_entry(
        self,
        path: Path,
        entry: Mapping[str, Any],
    ) -> None:
        with self._transcript_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    @staticmethod
    def _rotate_log(path: Path) -> None:
        try:
            if not path.is_file() or path.stat().st_size < LOG_ROTATE_BYTES:
                return
            oldest = path.with_suffix(path.suffix + f".{LOG_ROTATE_GENERATIONS}")
            if oldest.exists():
                oldest.unlink()
            for generation in range(LOG_ROTATE_GENERATIONS - 1, 0, -1):
                source = path.with_suffix(path.suffix + f".{generation}")
                target = path.with_suffix(path.suffix + f".{generation + 1}")
                if source.exists():
                    source.replace(target)
            path.replace(path.with_suffix(path.suffix + ".1"))
        except OSError:
            return


_RUNTIMES: dict[str, PluginProcessRuntime] = {}
_RUNTIMES_LOCK = threading.RLock()


def build_process_plugin(manifest: Mapping[str, Any], directory: Path) -> Plugin:
    """Create the in-core proxy for one validated API-v4 manifest."""

    key = str(manifest["plugin_key"])
    with _RUNTIMES_LOCK:
        runtime = _RUNTIMES.get(key)
        if runtime is None or runtime.directory != Path(directory).resolve():
            if runtime is not None:
                runtime.shutdown()
            runtime = PluginProcessRuntime(manifest, Path(directory))
            _RUNTIMES[key] = runtime
    capabilities = set(manifest.get("capabilities") or [])
    runtime_config = dict(manifest.get("runtime") or {})
    actions = set(runtime_config.get("actions") or (["start", "stop", "restart"] if "runtime_control" in capabilities else []))
    sidecar = dict(runtime_config.get("sidecar") or {})

    def call(operation: str, context: PluginContext, payload: Mapping[str, Any] | None = None) -> Any:
        if runtime._context is None:
            runtime.initialize(context)
        else:
            runtime._context = context
        return runtime.request(operation, payload)

    study_schema = manifest.get("study_settings_schema") or {}
    has_study_validator = any(
        isinstance(field, Mapping) and bool(str(field.get("format") or "").strip())
        for field in study_schema.values()
    )
    trial_events = set(runtime_config.get("trial_events") or [])

    def validate_study_setting(field_name: str, value: str) -> None:
        try:
            runtime.request(
                "validate_study_setting",
                {"field_name": field_name, "value": value},
            )
        except PluginProcessError as error:
            message = str(error)
            if message.startswith("ValueError: "):
                message = message[len("ValueError: "):]
            raise ValueError(message) from error

    return Plugin(
        key=key,
        label=str((manifest.get("ui") or {}).get("label") or key),
        category=str(manifest.get("category") or "plugin"),
        config_key=str(manifest.get("config_key") or key),
        can_start="start" in actions,
        can_stop="stop" in actions,
        can_restart="restart" in actions,
        can_toggle=runtime_config.get("can_toggle", True) is not False,
        has_lsl="lsl_stream_provider" in capabilities,
        has_recording="recording_source" in capabilities,
        initialize=lambda context: runtime.initialize(context),
        get_status=lambda context: call("status", context) or {},
        start=(lambda context: call("start", context)) if "start" in actions else None,
        stop=(lambda context: call("stop", context)) if "stop" in actions else None,
        restart=(lambda context: call("restart", context)) if "restart" in actions else None,
        run_admin_action=(
            lambda context, action, payload: call(
                "admin_action", context, {"action": action, "payload": payload}
            )
        ) if "admin_actions" in capabilities else None,
        run_participant_action=(
            lambda context, action, payload: call(
                "participant_action", context, {"action": action, "payload": payload}
            )
        ) if "participant_actions" in capabilities else None,
        ingest_participant=(
            lambda context, ingest, payload: call(
                "participant_ingest", context, {"ingest": ingest, "payload": payload}
            )
        ) if "participant_ingest" in capabilities else None,
        on_trial_start=(lambda context, options: call("trial_start", context, options))
        if "start" in trial_events else None,
        on_trial_stop=(lambda context, options: call("trial_stop", context, options))
        if "stop" in trial_events else None,
        on_trial_marker=(lambda context, options: call("trial_marker", context, options))
        if "marker" in trial_events else None,
        get_interval_summary=(
            lambda context, start, end: call(
                "interval_summary", context, {"start_epoch": start, "end_epoch": end}
            ) or {}
        ) if "interval_summary" in capabilities else None,
        export_interval_samples=(
            lambda context, start, end: call(
                "interval_export", context, {"start_epoch": start, "end_epoch": end}
            ) or []
        ) if "sidecar_export" in capabilities else None,
        publish_destination=(
            lambda context, payload: call("publish", context, {"payload": payload}) or {}
        ) if "upload_destination" in capabilities else None,
        validate_study_setting=validate_study_setting if has_study_validator else None,
        sidecar_sensor=sidecar.get("sensor"),
        sidecar_filename_suffix=sidecar.get("filename_suffix"),
        sidecar_output_key=sidecar.get("output_key"),
    )


def get_process_runtime(plugin_key: str) -> PluginProcessRuntime | None:
    with _RUNTIMES_LOCK:
        return _RUNTIMES.get(str(plugin_key or "").strip())


def shutdown_process_plugins() -> None:
    with _RUNTIMES_LOCK:
        runtimes = list(_RUNTIMES.values())
    for runtime in runtimes:
        runtime.shutdown()


def expire_process_console_unlocks(run_id: str | None = None) -> None:
    """Terminate intervention grants when their study run ends or changes."""

    with _RUNTIMES_LOCK:
        runtimes = list(_RUNTIMES.values())
    for runtime in runtimes:
        runtime.expire_console_unlock(run_id)


def reset_process_plugins() -> None:
    shutdown_process_plugins()
    with _RUNTIMES_LOCK:
        _RUNTIMES.clear()


def _serialize_context(context: PluginContext) -> dict[str, Any]:
    return {
        "base_dir": str(context.base_dir),
        "data_dir": str(context.data_dir),
        "hardware_config": deepcopy(context.hardware_config),
        "local_secrets": deepcopy(context.local_secrets),
        "local_secrets_file": str(context.local_secrets_file),
        "runtime_locked": bool(context.runtime_locked),
    }


atexit.register(shutdown_process_plugins)
