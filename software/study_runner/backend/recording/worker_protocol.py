"""Authenticated, idempotent loopback protocol for the bundled XDF worker."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping, Protocol
import urllib.error
import urllib.request
import uuid

from study_runner.backend.services.atomic_io import atomic_write_json

from .errors import (
    CommandConflictError,
    CommandInProgressError,
    WorkerProtocolError,
    WorkerUnavailableError,
)


WORKER_PROTOCOL_VERSION = 1
WORKER_STATE_SCHEMA = "study-runner/recording-worker-state/v1"
COMMAND_LEDGER_SCHEMA = "study-runner/worker-command-ledger/v1"
DEFAULT_WORKER_HOST = "127.0.0.1"


def _require_loopback(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("recording worker host must be a literal loopback address") from error
    if not address.is_loopback:
        raise ValueError("recording worker may only listen on loopback")
    return str(address)


@dataclass(frozen=True)
class WorkerEndpointState:
    session_id: str
    port: int
    token: str
    host: str = DEFAULT_WORKER_HOST
    pid: int | None = None
    generation: int = 1
    created_at_epoch: float = field(default_factory=time.time)
    last_seen_epoch: float | None = None
    backend_name: str = "native-xdf-worker"

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("worker session_id is required")
        _require_loopback(self.host)
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("worker port must be between 1 and 65535")
        if len(self.token) < 32:
            raise ValueError("worker session token is too short")
        if self.generation < 1:
            raise ValueError("worker generation must be positive")

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        port: int,
        host: str = DEFAULT_WORKER_HOST,
        pid: int | None = None,
        generation: int = 1,
        backend_name: str = "native-xdf-worker",
        clock: Callable[[], float] = time.time,
    ) -> "WorkerEndpointState":
        return cls(
            session_id=session_id,
            port=port,
            token=secrets.token_urlsafe(32),
            host=host,
            pid=pid,
            generation=generation,
            created_at_epoch=clock(),
            backend_name=backend_name,
        )

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKER_STATE_SCHEMA,
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "host": self.host,
            "port": self.port,
            "token": self.token,
            "pid": self.pid,
            "generation": self.generation,
            "created_at_epoch": self.created_at_epoch,
            "last_seen_epoch": self.last_seen_epoch,
            "backend_name": self.backend_name,
        }

    def public_dict(self) -> dict[str, Any]:
        """Return diagnostics without exposing the bearer token."""

        payload = self.as_dict()
        payload.pop("token", None)
        payload["token_fingerprint"] = hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:12]
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkerEndpointState":
        if payload.get("schema") != WORKER_STATE_SCHEMA:
            raise WorkerProtocolError("unsupported worker state schema")
        if int(payload.get("protocol_version") or 0) != WORKER_PROTOCOL_VERSION:
            raise WorkerProtocolError("unsupported worker protocol version")
        return cls(
            session_id=str(payload.get("session_id") or ""),
            host=str(payload.get("host") or ""),
            port=int(payload.get("port") or 0),
            token=str(payload.get("token") or ""),
            pid=int(payload["pid"]) if payload.get("pid") is not None else None,
            generation=int(payload.get("generation") or 0),
            created_at_epoch=float(payload.get("created_at_epoch") or 0.0),
            last_seen_epoch=(
                float(payload["last_seen_epoch"]) if payload.get("last_seen_epoch") is not None else None
            ),
            backend_name=str(payload.get("backend_name") or "native-xdf-worker"),
        )


class WorkerStateStore:
    """Atomic persistence for worker port, token and generation."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, state: WorkerEndpointState) -> None:
        atomic_write_json(self.path, state.as_dict())

    def load(self) -> WorkerEndpointState | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise WorkerProtocolError(f"worker state is unreadable: {self.path}") from error
        if not isinstance(payload, dict):
            raise WorkerProtocolError("worker state must be a JSON object")
        return WorkerEndpointState.from_dict(payload)

    def touch(self, *, clock: Callable[[], float] = time.time) -> WorkerEndpointState:
        state = self.load()
        if state is None:
            raise WorkerUnavailableError("worker endpoint state does not exist")
        updated = replace(state, last_seen_epoch=clock())
        self.save(updated)
        return updated


@dataclass(frozen=True)
class WorkerCommand:
    name: str
    payload: Mapping[str, Any]
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    protocol_version: int = WORKER_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("worker command name is required")
        if not self.command_id.strip():
            raise ValueError("worker command_id is required")
        # Fail early if a caller attempts to send a non-JSON command.
        _canonical_json(self.payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "command_id": self.command_id,
            "name": self.name,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkerCommand":
        if int(payload.get("protocol_version") or 0) != WORKER_PROTOCOL_VERSION:
            raise WorkerProtocolError("unsupported command protocol version")
        command_payload = payload.get("payload")
        if not isinstance(command_payload, dict):
            raise WorkerProtocolError("command payload must be an object")
        return cls(
            name=str(payload.get("name") or ""),
            payload=command_payload,
            command_id=str(payload.get("command_id") or ""),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(self.as_dict_without_id())).hexdigest()

    def as_dict_without_id(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "name": self.name,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class WorkerResponse:
    command_id: str
    ok: bool
    result: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    replayed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "command_id": self.command_id,
            "ok": self.ok,
            "result": dict(self.result),
            "error": self.error,
            "replayed": self.replayed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkerResponse":
        if int(payload.get("protocol_version") or 0) != WORKER_PROTOCOL_VERSION:
            raise WorkerProtocolError("unsupported response protocol version")
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            raise WorkerProtocolError("worker result must be an object")
        return cls(
            command_id=str(payload.get("command_id") or ""),
            ok=bool(payload.get("ok", False)),
            result=result,
            error=str(payload["error"]) if payload.get("error") else None,
            replayed=bool(payload.get("replayed", False)),
        )


class PersistentCommandLedger:
    """Records command outcomes before acknowledging them to the coordinator.

    A command id may be replayed only with byte-equivalent semantic content.
    If the process died while the entry was ``running``, the generic ledger
    refuses to guess whether side effects happened.  A recovery routine must
    reconcile worker/artifact state and issue a new command id.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()

    def execute(
        self,
        command: WorkerCommand,
        handler: Callable[[WorkerCommand], Mapping[str, Any] | WorkerResponse],
    ) -> WorkerResponse:
        with self._lock:
            document = self._load()
            existing = document["commands"].get(command.command_id)
            if existing is not None:
                return self._replay(command, existing)

            document["commands"][command.command_id] = {
                "fingerprint": command.fingerprint,
                "name": command.name,
                "state": "running",
                "started_at_epoch": self._clock(),
            }
            self._save(document)

            try:
                raw_result = handler(command)
                response = raw_result if isinstance(raw_result, WorkerResponse) else WorkerResponse(
                    command_id=command.command_id,
                    ok=True,
                    result=dict(raw_result),
                )
                if response.command_id != command.command_id:
                    raise WorkerProtocolError("handler returned a mismatched command_id")
            except Exception as error:
                entry = document["commands"][command.command_id]
                entry.update(
                    {
                        "state": "failed",
                        "finished_at_epoch": self._clock(),
                        "response": WorkerResponse(
                            command_id=command.command_id,
                            ok=False,
                            error=f"{type(error).__name__}: {error}",
                        ).as_dict(),
                    }
                )
                self._save(document)
                raise

            entry = document["commands"][command.command_id]
            entry.update(
                {
                    "state": "completed" if response.ok else "failed",
                    "finished_at_epoch": self._clock(),
                    "response": response.as_dict(),
                }
            )
            self._save(document)
            return response

    def _replay(self, command: WorkerCommand, entry: Mapping[str, Any]) -> WorkerResponse:
        if not hmac.compare_digest(str(entry.get("fingerprint") or ""), command.fingerprint):
            raise CommandConflictError(f"command_id {command.command_id!r} was reused with different content")
        if entry.get("state") == "running":
            raise CommandInProgressError(f"command {command.command_id!r} needs recovery reconciliation")
        response_payload = entry.get("response")
        if not isinstance(response_payload, dict):
            raise WorkerProtocolError("completed command has no persisted response")
        return replace(WorkerResponse.from_dict(response_payload), replayed=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": COMMAND_LEDGER_SCHEMA, "commands": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise WorkerProtocolError(f"command ledger is unreadable: {self.path}") from error
        if not isinstance(payload, dict) or payload.get("schema") != COMMAND_LEDGER_SCHEMA:
            raise WorkerProtocolError("unsupported command ledger schema")
        if not isinstance(payload.get("commands"), dict):
            raise WorkerProtocolError("command ledger commands must be an object")
        return payload

    def _save(self, document: Mapping[str, Any]) -> None:
        atomic_write_json(self.path, document)


class WorkerTransport(Protocol):
    def __call__(
        self,
        endpoint: WorkerEndpointState,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class LoopbackWorkerClient:
    """Narrow worker client; retries remain safe when command_id is reused."""

    def __init__(
        self,
        endpoint: WorkerEndpointState,
        *,
        transport: WorkerTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        _require_loopback(endpoint.host)
        self.endpoint = endpoint
        self.transport = transport or _urllib_transport
        self.timeout_seconds = timeout_seconds

    def send(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        command_id: str | None = None,
    ) -> WorkerResponse:
        command = WorkerCommand(name=name, payload=payload, command_id=command_id or str(uuid.uuid4()))
        body = _canonical_json(command.as_dict())
        headers = {
            "Authorization": f"Bearer {self.endpoint.token}",
            "Content-Type": "application/json",
            "X-Study-Runner-Session": self.endpoint.session_id,
        }
        try:
            raw_response = self.transport(self.endpoint, body, headers, self.timeout_seconds)
        except WorkerProtocolError:
            raise
        except Exception as error:
            raise WorkerUnavailableError(f"recording worker is unavailable: {error}") from error
        response = WorkerResponse.from_dict(raw_response)
        if response.command_id != command.command_id:
            raise WorkerProtocolError("worker response command_id does not match request")
        return response


class WorkerCommandRouter:
    """Transport-independent authenticated worker-side command dispatcher."""

    def __init__(
        self,
        *,
        token: str,
        ledger: PersistentCommandLedger,
        handlers: Mapping[str, Callable[[WorkerCommand], Mapping[str, Any] | WorkerResponse]],
        session_id: str | None = None,
    ) -> None:
        self._token = token
        self._ledger = ledger
        self._handlers = dict(handlers)
        self._session_id = str(session_id or "").strip()

    def handle(
        self,
        authorization: str,
        payload: Mapping[str, Any],
        *,
        session_id: str | None = None,
    ) -> WorkerResponse:
        supplied = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied, self._token):
            raise WorkerProtocolError("invalid worker bearer token")
        supplied_session = str(session_id or "").strip()
        if self._session_id and not hmac.compare_digest(supplied_session, self._session_id):
            raise WorkerProtocolError("worker request session does not match its endpoint")
        command = WorkerCommand.from_dict(payload)
        command_session = str(command.payload.get("session_id") or "").strip()
        if self._session_id and not hmac.compare_digest(command_session, self._session_id):
            raise WorkerProtocolError("worker command session does not match its endpoint")
        handler = self._handlers.get(command.name)
        if handler is None:
            raise WorkerProtocolError(f"unknown worker command: {command.name}")
        return self._ledger.execute(command, handler)


def _urllib_transport(
    endpoint: WorkerEndpointState,
    body: bytes,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        f"{endpoint.base_url}/v1/commands",
        data=body,
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise WorkerUnavailableError(str(error)) from error
    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise WorkerProtocolError("worker returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise WorkerProtocolError("worker response must be an object")
    return payload


def _canonical_json(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise WorkerProtocolError(f"worker payload is not strict JSON: {error}") from error
