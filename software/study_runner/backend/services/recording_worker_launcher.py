"""Detached process launcher for the session-scoped recording worker."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable

from ..recording.artifacts import ArtifactPaths
from ..recording.errors import WorkerUnavailableError
from ..recording.worker_binary import WorkerBinaryAvailability
from ..recording.worker_protocol import (
    LoopbackWorkerClient,
    WorkerEndpointState,
    WorkerStateStore,
)
from .recording_runtime_support import (
    DEFAULT_WORKER_START_TIMEOUT_SECONDS,
    RECORDING_COMMAND_TIMEOUT_SECONDS,
    reserve_loopback_port,
)


@dataclass(frozen=True)
class WorkerLaunchSpec:
    """Resolved control-plane and native-core resources for one worker launch."""

    availability: WorkerBinaryAvailability
    resource_root: Path


class DetachedWorkerLauncher:
    """Launch the detached hybrid worker from a token-bearing state file."""

    def __init__(
        self,
        worker: WorkerLaunchSpec | Path,
        *,
        popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
        start_timeout_seconds: float = DEFAULT_WORKER_START_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        client_factory: Callable[..., LoopbackWorkerClient] = LoopbackWorkerClient,
    ) -> None:
        if isinstance(worker, WorkerLaunchSpec):
            self.spec = worker
        else:
            legacy = Path(worker).resolve()
            self.spec = WorkerLaunchSpec(
                availability=WorkerBinaryAvailability(
                    available=True,
                    path=legacy,
                    protocol_version=1,
                    kind="legacy_external_worker",
                ),
                resource_root=legacy.parent,
            )
        self._popen = popen
        self._start_timeout = max(0.1, float(start_timeout_seconds))
        self._clock = clock
        self._sleeper = sleeper
        self._client_factory = client_factory

    def launch(
        self,
        paths: ArtifactPaths,
        *,
        generation: int = 1,
    ) -> tuple[WorkerEndpointState, LoopbackWorkerClient]:
        port = reserve_loopback_port()
        endpoint = WorkerEndpointState.create(
            session_id=paths.identity.session_id,
            port=port,
            generation=generation,
        )
        WorkerStateStore(paths.worker_state_file).save(endpoint)
        log_path = paths.logs_dir / "recording-worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        creationflags = 0
        startupinfo = None
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        command, environment = self._command()
        command.extend(
            [
                "--state-file",
                str(paths.worker_state_file),
                "--session-dir",
                str(paths.root),
                "--lease-seconds",
                "900",
            ]
        )
        with log_path.open("ab") as log_handle:
            process = self._popen(
                command,
                cwd=str(paths.root),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=creationflags,
                startupinfo=startupinfo,
                start_new_session=start_new_session,
                env=environment,
            )
        endpoint = WorkerEndpointState(**{**endpoint.__dict__, "pid": int(process.pid)})
        WorkerStateStore(paths.worker_state_file).save(endpoint)
        client = self._client_factory(
            endpoint,
            timeout_seconds=RECORDING_COMMAND_TIMEOUT_SECONDS,
        )
        deadline = self._clock() + self._start_timeout
        last_error = "worker did not answer"
        while self._clock() < deadline:
            if process.poll() is not None:
                last_error = f"worker exited with code {process.returncode}"
                break
            try:
                response = client.send(
                    "health",
                    {
                        "session_id": paths.identity.session_id,
                        "generation": generation,
                    },
                    command_id=f"health-start-{paths.identity.session_id}-g{generation}",
                )
                if response.ok:
                    WorkerStateStore(paths.worker_state_file).touch()
                    return endpoint, client
                last_error = response.error or "worker health command failed"
            except Exception as error:  # worker needs a short startup window
                last_error = str(error)
            self._sleeper(0.1)
        try:
            process.terminate()
        except OSError:
            pass
        raise WorkerUnavailableError(f"recording worker failed to start: {last_error}")

    def _command(self) -> tuple[list[str], dict[str, str]]:
        availability = self.spec.availability
        environment = dict(os.environ)
        if availability.kind == "legacy_external_worker":
            if availability.path is None:
                raise WorkerUnavailableError("legacy recording worker path is missing")
            return [str(availability.path)], environment
        if (
            availability.kind != "hybrid_core"
            or availability.core_path is None
            or not availability.canonical_xdf
            or not availability.supports_merge
        ):
            raise WorkerUnavailableError(
                availability.reason or "canonical native XDF core is unavailable"
            )
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--recording-worker"]
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        else:
            server_script = (self.spec.resource_root / "server.py").resolve()
            if not server_script.is_file():
                raise WorkerUnavailableError(
                    f"recording worker entrypoint is missing: {server_script}"
                )
            command = [sys.executable, str(server_script), "--recording-worker"]
        command.extend(["--xdf-core", str(availability.core_path)])
        return command, environment
