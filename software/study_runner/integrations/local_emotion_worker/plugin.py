from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..plugin_api import IntegrationContext, IntegrationPlugin


EMOTION_WORKER_MODES = {"local_worker", "remote_worker"}
DEFAULT_WORKER = {
    "script_path": "study_runner/integrations/local_emotion_worker/server.py",
    "log_dir": "study_runner/integrations/local_emotion_worker/logs",
    "model_assets_dir": "study_runner/integrations/local_emotion_worker/model_assets",
    "deepface_home": "study_runner/integrations/local_emotion_worker/deepface_home",
}
DEEPFACE_EMOTION_MODEL = {
    "name": "facial_expression_model_weights.h5",
    "url": "https://github.com/serengil/deepface_models/releases/download/v1.0/facial_expression_model_weights.h5",
    "min_bytes": 1_000_000,
}

_lock = threading.Lock()
_process: subprocess.Popen[Any] | None = None
_log_handle: Any = None
_config: dict[str, Any] = {}
_registered_shutdown = False
_install_lock = threading.Lock()
_install_job: dict[str, Any] = {
    "running": False,
    "last_message": "Dependency repair has not been run.",
}
_model_lock = threading.Lock()
_model_job: dict[str, Any] = {
    "running": False,
    "last_message": "Model asset repair has not been run.",
}


def ensure_started(context: IntegrationContext) -> dict[str, Any]:
    """Start the local worker when camera emotion is enabled and auto_start is set."""
    _configure(context)
    if not _config.get("worker_enabled") or _config.get("worker_mode") != "local_worker":
        return _status(context)
    if not _config.get("configured_enabled"):
        return _status(context)
    if not _config.get("auto_start", True):
        return _status(context)
    return _start(context)


def stop_worker(context: IntegrationContext) -> dict[str, Any]:
    """Stop the managed local worker process."""
    return _stop(context)


def install_dependencies(context: IntegrationContext) -> dict[str, Any]:
    """Compatibility alias for the full DeepFace runtime repair."""
    return repair_runtime(context)


def repair_runtime(context: IntegrationContext) -> dict[str, Any]:
    """Repair Python packages and DeepFace model assets in the worker environment."""
    _configure(context)
    with _install_lock:
        install_running = bool(_install_job.get("running"))
    with _model_lock:
        model_running = bool(_model_job.get("running"))
    if install_running or model_running:
        return _repair_status()

    with _install_lock:
        _install_job.clear()
        _install_job.update(
            {
                "running": True,
                "status": "running",
                "started_at": _timestamp(),
                "finished_at": None,
                "return_code": None,
                "last_message": "Installing Study Runner dependencies for the Local Emotion Worker.",
                "output_tail": "",
                "python_executable": str(_config.get("python_executable") or sys.executable),
                "requirements_file": str(context.base_dir / "requirements.txt"),
            }
        )
    with _model_lock:
        _model_job.clear()
        _model_job.update(
            {
                "running": False,
                "status": "queued",
                "started_at": None,
                "finished_at": None,
                "last_message": "Waiting for dependency install before checking DeepFace model weights.",
                "output_tail": "",
                "asset_name": DEEPFACE_EMOTION_MODEL["name"],
                "asset_path": str(_model_asset_path()),
                "asset_url": DEEPFACE_EMOTION_MODEL["url"],
                "bytes_downloaded": 0,
                "total_bytes": 0,
            }
        )

    thread = threading.Thread(target=_run_runtime_repair, args=(context,), daemon=True)
    thread.start()
    return _repair_status()


def _config_section(context: IntegrationContext) -> dict[str, Any]:
    config = context.hardware_config.get("camera_emotion") or context.hardware_config.get("camera") or {}
    return config if isinstance(config, dict) else {}


def _configure(context: IntegrationContext) -> None:
    global _config

    config = _config_section(context)
    worker_config = config.get("emotion_worker") if isinstance(config.get("emotion_worker"), dict) else {}
    worker_mode = str(config.get("worker_mode") or "local_worker")
    worker_url = str(config.get("emotion_worker_url") or "http://127.0.0.1:3001").rstrip("/")
    host, port = _host_port_from_url(worker_url)
    script_path = context.resolve_project_path(
        context.resolve_platform_value(worker_config.get("script_path")) or DEFAULT_WORKER["script_path"]
    )
    log_dir = Path(
        context.resolve_project_path(context.resolve_platform_value(worker_config.get("log_dir")) or DEFAULT_WORKER["log_dir"])
        or context.base_dir / DEFAULT_WORKER["log_dir"]
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    deepface_home = context.resolve_project_path(context.resolve_platform_value(worker_config.get("deepface_home")))
    if not deepface_home:
        deepface_home = context.resolve_project_path(DEFAULT_WORKER["deepface_home"])
    # DeepFace treats DEEPFACE_HOME as the PARENT of `.deepface` and appends
    # `.deepface/weights` itself. Guard against a value that already ends in
    # `.deepface`, which would otherwise produce a doubled `.deepface/.deepface`
    # path (weights never found -> re-download at warmup -> failure).
    if deepface_home and Path(deepface_home).name == ".deepface":
        deepface_home = str(Path(deepface_home).parent)
    model_cache_dir = context.resolve_project_path(context.resolve_platform_value(worker_config.get("model_cache_dir")))
    if not model_cache_dir:
        model_cache_dir = str(Path(deepface_home) / ".deepface" / "weights")
    model_assets_dir = context.resolve_project_path(
        context.resolve_platform_value(worker_config.get("model_assets_dir")) or DEFAULT_WORKER["model_assets_dir"]
    )

    _config = {
        "configured_enabled": bool(config.get("enabled", False)),
        "worker_enabled": worker_mode in EMOTION_WORKER_MODES,
        "worker_mode": worker_mode,
        "url": worker_url,
        "host": host,
        "port": port,
        "auto_start": bool(worker_config.get("auto_start", True)),
        "python_executable": context.resolve_project_path(context.resolve_platform_value(worker_config.get("python_executable")))
        or sys.executable,
        "script_path": script_path,
        "log_dir": str(log_dir),
        "raw_log_path": str(log_dir / "emotion_worker_runtime.log"),
        "timeout_seconds": max(0.25, float(worker_config.get("health_timeout_seconds", 0.75))),
        "deepface_home": deepface_home,
        "model_cache_dir": model_cache_dir,
        "model_assets_dir": model_assets_dir,
    }


def _initialize(context: IntegrationContext) -> None:
    ensure_started(context)


def _status(context: IntegrationContext) -> dict[str, Any]:
    if not _config:
        _configure(context)

    worker_enabled = bool(_config.get("worker_enabled"))
    running = _is_running()
    status = {
        "configured_enabled": bool(_config.get("configured_enabled", False)),
        "runtime_enabled": running,
        "enabled": worker_enabled,
        "status": "disabled" if not worker_enabled else ("running" if running else "stopped"),
        "worker_mode": _config.get("worker_mode", "local_worker"),
        "url": _config.get("url", ""),
        "host": _config.get("host", "127.0.0.1"),
        "port": _config.get("port", 3001),
        "raw_log_path": _config.get("raw_log_path"),
        "dependency_install": _dependency_install_status(),
        "model_asset_install": _model_asset_install_status(),
        "last_message": "Emotion Worker is available for diagnostics. Camera emotion recording is controlled by study settings.",
        "device_label": "Local Emotion Worker",
    }
    if not worker_enabled:
        status["last_message"] = "Emotion Worker is disabled because camera_emotion.worker_mode is not local_worker or remote_worker."
        return status
    if not _config.get("url"):
        return {**status, "status": "not_configured", "last_message": "camera_emotion.emotion_worker_url is not configured."}

    payload, error = _probe_worker(_config["url"], timeout=float(_config.get("timeout_seconds", 0.75)))
    if error:
        return {
            **status,
            "status": "starting" if running else "unreachable",
            "runtime_enabled": running,
            "last_message": f"Could not reach local Emotion Worker: {error}",
        }

    ready = bool(payload.get("ready", payload.get("ok", False)))
    model_error = str(payload.get("model_error") or "").strip()
    if model_error:
        error_info = _model_error_info(payload)
        return {
            **status,
            "runtime_enabled": running,
            "status": "failed",
            "connected": ready,
            "latest": payload,
            "model_error_class": error_info["model_error_class"],
            "model_asset_name": error_info.get("model_asset_name"),
            "model_asset_url": error_info.get("model_asset_url"),
            "model_asset_path": error_info.get("model_asset_path"),
            "suggested_action": error_info.get("suggested_action"),
            "last_message": _runtime_error_message(error_info),
        }
    return {
        **status,
        "runtime_enabled": ready or running,
        "status": "connected" if ready else "starting",
        "connected": ready,
        "latest": payload,
        "last_message": str(payload.get("message") or ("Emotion Worker ready." if ready else "Emotion Worker responded but is not ready.")),
    }


def _start(context: IntegrationContext) -> Any:
    global _process, _log_handle, _registered_shutdown

    _configure(context)
    if not _config.get("worker_enabled"):
        return _status(context)
    if _config.get("worker_mode") != "local_worker":
        return _status(context)

    if _is_running():
        return _status(context)

    script_path = Path(str(_config.get("script_path") or ""))
    if not script_path.exists():
        return {
            **_status(context),
            "status": "not_configured",
            "last_message": f"Emotion Worker script not found: {script_path}",
        }

    _ensure_local_model_weights()

    command = [
        str(_config.get("python_executable") or sys.executable),
        str(script_path),
        "--host",
        str(_config.get("host", "127.0.0.1")),
        "--port",
        str(_config.get("port", 3001)),
    ]
    env = os.environ.copy()
    if _config.get("deepface_home"):
        env["DEEPFACE_HOME"] = str(_config["deepface_home"])
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    start_error = ""
    with _lock:
        try:
            _log_handle = Path(str(_config["raw_log_path"])).open("a", encoding="utf-8")
            _log_handle.write(f"\n[{_timestamp()}] starting: {' '.join(command)}\n")
            _log_handle.flush()
            _process = subprocess.Popen(
                command,
                cwd=str(script_path.parent),
                stdout=_log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                creationflags=creationflags,
            )
        except OSError as error:
            _process = None
            start_error = str(error)

        if not start_error and not _registered_shutdown:
            atexit.register(_stop_process)
            _registered_shutdown = True

    if start_error:
        _close_log_handle()
        return {**_status(context), "status": "failed", "last_message": start_error}

    time.sleep(0.35)
    return _status(context)


def _stop(context: IntegrationContext) -> Any:
    _configure(context)
    _stop_process()
    return _status(context)


def _restart(context: IntegrationContext) -> Any:
    _configure(context)
    _stop_process()
    return _start(context)


def _stop_process() -> None:
    global _process

    with _lock:
        process = _process
    if process is None or process.poll() is not None:
        with _lock:
            _process = None
        _close_log_handle()
        return

    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    finally:
        with _lock:
            if _process is process:
                _process = None
        _close_log_handle()


def _is_running() -> bool:
    with _lock:
        return _process is not None and _process.poll() is None


def _probe_worker(url: str, *, timeout: float) -> tuple[dict[str, Any], str | None]:
    request = urllib.request.Request(f"{url.rstrip('/')}/status", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {}, str(error)
    return (payload if isinstance(payload, dict) else {}), None


def _host_port_from_url(url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(url or "http://127.0.0.1:3001")
    return parsed.hostname or "127.0.0.1", int(parsed.port or 3001)


def _model_error_info(payload: dict[str, Any]) -> dict[str, Any]:
    error = str(payload.get("model_error") or "").strip()
    error_class = str(payload.get("model_error_class") or "").strip() or _classify_model_error(error)
    return {
        "model_error": error,
        "model_error_class": error_class,
        "model_asset_name": payload.get("model_asset_name") or DEEPFACE_EMOTION_MODEL["name"],
        "model_asset_url": payload.get("model_asset_url") or DEEPFACE_EMOTION_MODEL["url"],
        "model_asset_path": payload.get("model_asset_path") or str(_model_asset_path()),
        "suggested_action": payload.get("suggested_action") or _suggested_action(error_class),
    }


def _classify_model_error(error: str) -> str:
    normalized = error.lower()
    if "no module named" in normalized or "tf-keras" in normalized or ("tensorflow" in normalized and "requires" in normalized):
        return "missing_package"
    if DEEPFACE_EMOTION_MODEL["name"].lower() in normalized and "downloading" in normalized:
        return "model_download_failed"
    if DEEPFACE_EMOTION_MODEL["name"].lower() in normalized:
        return "model_file_missing"
    return "model_warmup_failed"


def _suggested_action(error_class: str) -> str:
    if error_class == "missing_package":
        return "Run the dashboard action 'Repair DeepFace runtime' or run 'pip install -r software/requirements.txt'."
    if error_class in {"model_download_failed", "model_file_missing", "model_file_unreadable"}:
        return (
            "Run the dashboard action 'Repair DeepFace runtime', or manually place "
            f"{DEEPFACE_EMOTION_MODEL['name']} at {_model_asset_path()}."
        )
    return "Run the dashboard action 'Repair DeepFace runtime' and restart the Local Emotion Worker."


def _runtime_error_message(error_info: dict[str, Any]) -> str:
    error_class = str(error_info.get("model_error_class") or "model_warmup_failed")
    detail = str(error_info.get("model_error") or "")
    if error_class == "missing_package":
        return (
            "Local Emotion Worker is reachable, but Python packages for DeepFace are missing or incompatible. "
            "Use the dashboard button 'Repair DeepFace runtime' or run 'pip install -r software/requirements.txt', "
            f"then restart the worker. Detail: {detail}"
        )
    if error_class in {"model_download_failed", "model_file_missing", "model_file_unreadable"}:
        return (
            "Local Emotion Worker is reachable, but the DeepFace emotion model weights are missing or could not be prepared. "
            "Use the dashboard button 'Repair DeepFace runtime'. If the download is blocked, manually place "
            f"{error_info.get('model_asset_name') or DEEPFACE_EMOTION_MODEL['name']} at "
            f"{error_info.get('model_asset_path') or _model_asset_path()}. Detail: {detail}"
        )
    return f"Local Emotion Worker is reachable, but DeepFace warmup failed. Detail: {detail}"


def _run_runtime_repair(context: IntegrationContext) -> None:
    dependency_ok = _run_dependency_install(context)
    if not dependency_ok:
        with _model_lock:
            _model_job.update(
                {
                    "running": False,
                    "status": "skipped",
                    "finished_at": _timestamp(),
                    "last_message": "Model asset repair skipped because dependency install failed.",
                }
            )
        return

    model_ok = _run_model_asset_install()
    if model_ok:
        _restart(context)


def _run_dependency_install(context: IntegrationContext) -> bool:
    python_executable = str(_config.get("python_executable") or sys.executable)
    requirements_file = Path(str(context.base_dir / "requirements.txt"))
    command = [
        python_executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(requirements_file),
    ]
    output = ""
    return_code = 1
    message = ""

    if not requirements_file.exists():
        message = f"Requirements file not found: {requirements_file}"
    else:
        try:
            result = subprocess.run(
                command,
                cwd=str(context.base_dir),
                capture_output=True,
                text=True,
                timeout=1800,
            )
            return_code = int(result.returncode)
            output = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
            if return_code == 0:
                message = "Dependencies installed. Checking DeepFace model weights next."
            else:
                message = "Dependency install failed. See output_tail."
        except Exception as error:
            message = f"Dependency install failed: {error}"
            output = str(error)

    with _install_lock:
        _install_job.update(
            {
                "running": False,
                "status": "completed" if return_code == 0 else "failed",
                "finished_at": _timestamp(),
                "return_code": return_code,
                "last_message": message,
                "output_tail": output[-8000:],
                "command": " ".join(command),
            }
        )
    return return_code == 0


def _run_model_asset_install() -> bool:
    asset_path = _model_asset_path()
    bundled_path = _bundled_model_asset_path()
    with _model_lock:
        _model_job.update(
            {
                "running": True,
                "status": "running",
                "started_at": _timestamp(),
                "finished_at": None,
                "last_message": "Checking DeepFace emotion model weights.",
                "asset_name": DEEPFACE_EMOTION_MODEL["name"],
                "asset_path": str(asset_path),
                "asset_url": DEEPFACE_EMOTION_MODEL["url"],
                "bundled_asset_path": str(bundled_path),
                "bytes_downloaded": 0,
                "total_bytes": 0,
                "output_tail": "",
            }
        )

    lines: list[str] = []
    status = "failed"
    message = ""
    try:
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        if _asset_is_valid(asset_path):
            status = "completed"
            message = f"DeepFace model weights already available at {asset_path}."
        elif _asset_is_valid(bundled_path):
            shutil.copy2(bundled_path, asset_path)
            status = "completed"
            message = f"DeepFace model weights copied from bundled asset to {asset_path}."
        else:
            if bundled_path.exists():
                lines.append(f"Bundled asset is present but too small or unreadable: {bundled_path}")
            lines.append(f"Downloading {DEEPFACE_EMOTION_MODEL['url']}")
            _download_model_asset(asset_path, lines)
            status = "completed"
            message = f"DeepFace model weights downloaded to {asset_path}."
    except Exception as error:
        message = f"DeepFace model asset repair failed: {error}"
        lines.append(message)

    with _model_lock:
        _model_job.update(
            {
                "running": False,
                "status": status,
                "finished_at": _timestamp(),
                "last_message": message,
                "output_tail": "\n".join(lines)[-8000:],
            }
        )
    return status == "completed"


def _download_model_asset(destination: Path, lines: list[str]) -> None:
    temporary = destination.with_name(f"{destination.name}.tmp")
    request = urllib.request.Request(
        DEEPFACE_EMOTION_MODEL["url"],
        headers={"User-Agent": "MRG-StudyRunner/DeepFaceRuntimeRepair"},
    )
    bytes_downloaded = 0
    total_bytes = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total_bytes = int(response.headers.get("Content-Length") or 0)
            with temporary.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    bytes_downloaded += len(chunk)
                    with _model_lock:
                        _model_job.update(
                            {
                                "bytes_downloaded": bytes_downloaded,
                                "total_bytes": total_bytes,
                                "last_message": f"Downloading DeepFace model weights: {bytes_downloaded} bytes.",
                            }
                        )
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    if bytes_downloaded < int(DEEPFACE_EMOTION_MODEL["min_bytes"]):
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(
            f"Downloaded model asset is too small ({bytes_downloaded} bytes). "
            f"Expected at least {DEEPFACE_EMOTION_MODEL['min_bytes']} bytes."
        )
    temporary.replace(destination)
    lines.append(f"Downloaded {bytes_downloaded} bytes to {destination}.")


def _ensure_local_model_weights() -> None:
    """Seed the integration-local DeepFace cache from the vendored model asset.

    Runs before the worker warms up so DeepFace finds the weights in the
    integration folder (never in ~/.deepface) and never needs a network download.
    A no-op once the cache is populated.
    """
    try:
        asset_path = _model_asset_path()
        if _asset_is_valid(asset_path):
            return
        bundled = _bundled_model_asset_path()
        if _asset_is_valid(bundled):
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled, asset_path)
    except Exception:
        # Best effort: if this fails, warmup surfaces a precise, actionable error.
        pass


def _asset_is_valid(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= int(DEEPFACE_EMOTION_MODEL["min_bytes"])
    except OSError:
        return False


def _model_asset_path() -> Path:
    return Path(str(_config.get("model_cache_dir") or Path.home() / ".deepface" / "weights")) / DEEPFACE_EMOTION_MODEL["name"]


def _bundled_model_asset_path() -> Path:
    return Path(str(_config.get("model_assets_dir") or "")) / DEEPFACE_EMOTION_MODEL["name"]


def _dependency_install_status() -> dict[str, Any]:
    with _install_lock:
        return dict(_install_job)


def _model_asset_install_status() -> dict[str, Any]:
    with _model_lock:
        return dict(_model_job)


def _repair_status() -> dict[str, Any]:
    return {
        "dependency_install": _dependency_install_status(),
        "model_asset_install": _model_asset_install_status(),
    }


def _close_log_handle() -> None:
    global _log_handle
    if _log_handle is None:
        return
    try:
        _log_handle.close()
    except Exception:
        pass
    _log_handle = None


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


PLUGIN = IntegrationPlugin(
    key="emotion_worker",
    label="Local Emotion Worker",
    category="processing",
    config_key="camera_emotion",
    can_start=True,
    can_stop=True,
    can_restart=True,
    can_toggle=False,
    initialize=_initialize,
    get_status=_status,
    start=_start,
    stop=_stop,
    restart=_restart,
)
