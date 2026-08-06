"""Loopback-only HTTP host for the detached recording worker process."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from study_runner.recording.worker_protocol import (
    LoopbackWorkerClient,
    PersistentCommandLedger,
    WorkerCommandRouter,
    WorkerEndpointState,
    WorkerProtocolError,
    WorkerResponse,
    WorkerStateStore,
)

from .runtime import RecordingWorkerRuntime


MAXIMUM_COMMAND_BYTES = 2 * 1024 * 1024


class _WorkerHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def _handler_type(
    router: WorkerCommandRouter,
    shutdown_event: Any,
) -> type[BaseHTTPRequestHandler]:
    class WorkerRequestHandler(BaseHTTPRequestHandler):
        server_version = "StudyRunnerRecordingWorker/1"
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/v1/commands":
                self._write_json(404, {"error": "not found"})
                return
            try:
                remote = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                self._write_json(403, {"error": "loopback requests only"})
                return
            if not remote.is_loopback:
                self._write_json(403, {"error": "loopback requests only"})
                return
            if self.headers.get("Transfer-Encoding"):
                self._write_json(400, {"error": "chunked requests are not supported"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "")
            except ValueError:
                self._write_json(400, {"error": "valid Content-Length is required"})
                return
            if length < 2 or length > MAXIMUM_COMMAND_BYTES:
                self._write_json(413, {"error": "command body is empty or too large"})
                return
            if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip() != "application/json":
                self._write_json(415, {"error": "application/json is required"})
                return
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._write_json(400, {"error": "request body is not strict UTF-8 JSON"})
                return
            if not isinstance(payload, dict):
                self._write_json(400, {"error": "command body must be an object"})
                return
            command_id = str(payload.get("command_id") or "")
            try:
                response = router.handle(
                    str(self.headers.get("Authorization") or ""),
                    payload,
                    session_id=str(self.headers.get("X-Study-Runner-Session") or ""),
                )
            except WorkerProtocolError as error:
                status = 403 if "bearer token" in str(error) else 400
                self._write_json(status, {"error": str(error), "command_id": command_id})
                return
            except Exception as error:
                response = WorkerResponse(
                    command_id=command_id,
                    ok=False,
                    error=f"{type(error).__name__}: {error}",
                )
            self._write_json(200, response.as_dict())
            if response.ok and payload.get("name") == "shutdown_session":
                shutdown_event.set()

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._write_json(404, {"error": "not found"})

        def _write_json(self, status: int, payload: Mapping[str, Any]) -> None:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *args: Any) -> None:
            return

    return WorkerRequestHandler


def run_recording_worker(
    *,
    state_file: Path,
    session_dir: Path,
    core_path: Path,
    lease_seconds: float = 900.0,
) -> int:
    """Run until explicit shutdown or replacement by a newer generation."""

    state_path = Path(state_file).resolve()
    session_root = Path(session_dir).resolve()
    endpoint = WorkerStateStore(state_path).load()
    if endpoint is None:
        raise RuntimeError("recording worker state file does not exist")
    if not state_path.is_relative_to(session_root):
        raise RuntimeError("recording worker state is outside its session")
    runtime = RecordingWorkerRuntime(
        session_id=endpoint.session_id,
        session_dir=session_root,
        state_file=state_path,
        generation=endpoint.generation,
        token=endpoint.token,
        core_path=Path(core_path),
        lease_seconds=lease_seconds,
    )
    ledger = PersistentCommandLedger(
        session_root / "logs" / f"recording-worker-commands-g{endpoint.generation}.json"
    )
    router = WorkerCommandRouter(
        token=endpoint.token,
        ledger=ledger,
        handlers=runtime.handlers,
        session_id=endpoint.session_id,
    )
    server = _WorkerHttpServer(
        (endpoint.host, endpoint.port),
        _handler_type(router, runtime.shutdown_event),
    )
    server.timeout = 0.5
    try:
        while not runtime.shutdown_event.is_set():
            server.handle_request()
        return 0
    finally:
        server.server_close()
        runtime.abort_on_worker_failure()
        runtime.close_monitor()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Study Runner recording worker")
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--xdf-core", required=True, type=Path)
    parser.add_argument("--lease-seconds", type=float, default=900.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        return run_recording_worker(
            state_file=arguments.state_file,
            session_dir=arguments.session_dir,
            core_path=arguments.xdf_core,
            lease_seconds=arguments.lease_seconds,
        )
    except Exception as error:
        print(f"recording worker failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
