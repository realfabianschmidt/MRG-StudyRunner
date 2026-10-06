"""Real-browser smoke test for choosing one waiting participant connection.

Run from software/: python tests/browser/participant_start_flow.py
Requires Chrome or Edge and the optional ``websockets`` Python package.
Set STUDY_RUNNER_BROWSER_PATH to use another browser executable.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from itertools import count
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from websockets.asyncio.client import connect
except ImportError as error:
    raise SystemExit("Install the optional websockets package to run this browser smoke test.") from error

_request_ids = count(1)


def browser_path() -> Path:
    candidates = [
        os.environ.get("STUDY_RUNNER_BROWSER_PATH", ""),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit("Chrome or Edge was not found; set STUDY_RUNNER_BROWSER_PATH.")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def json_request(url: str, payload: dict | None = None, *, method: str | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, method=method or ("POST" if body is not None else "GET"))
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=15) as response:
        return json.load(response)


async def until(check, *, seconds: float = 20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(0.25)
    raise AssertionError("Timed out waiting for the browser or server state.")


async def cdp(websocket, method: str, params: dict):
    request_id = next(_request_ids)
    await websocket.send(json.dumps({
        "id": request_id, "method": method, "params": params,
    }))
    while True:
        message = json.loads(await websocket.recv())
        if message.get("id") == request_id:
            if "error" in message:
                raise AssertionError(message["error"])
            if "exceptionDetails" in message.get("result", {}):
                raise AssertionError(message["result"]["exceptionDetails"])
            return message.get("result", {})


async def evaluate(websocket, expression: str):
    result = await cdp(websocket, "Runtime.evaluate", {
        "expression": expression, "returnByValue": True,
    })
    return result.get("result", {}).get("value")


async def run(base_url: str, debugger_url: str) -> None:
    def open_tab():
        page = json_request(f"{debugger_url}/json/new?{quote(base_url + '/', safe='')}", method="PUT")
        return page["webSocketDebuggerUrl"]

    async with connect(open_tab()) as first, connect(open_tab()) as second:
        print("Opened two participant browser tabs.", flush=True)
        async def waiting_pages():
            status = json_request(base_url + "/api/admin/status")
            clients = [client for client in status["study_clients"]["clients"]
                       if client["study_id"] == "browser-smoke" and client["waiting_for_admin_start"]]
            return clients if len(clients) == 2 else None

        clients = await until(waiting_pages)
        print("Both tabs reached the waiting room.", flush=True)
        first_id = await evaluate(first, "sessionStorage.getItem('study-runner-client-id')")
        second_id = await evaluate(second, "sessionStorage.getItem('study-runner-client-id')")
        assert first_id and second_id and first_id != second_id
        assert {client["client_id"] for client in clients} == {first_id, second_id}
        assert await evaluate(first, "document.getElementById('screen-waiting').classList.contains('active')")
        assert await evaluate(second, "document.getElementById('screen-waiting').classList.contains('active')")
        assert await evaluate(first, "document.getElementById('study-device-id').textContent")
        assert await evaluate(second, "document.getElementById('study-device-id').textContent")

        selected = json_request(base_url + "/api/admin/study-run/target", {"client_id": second_id})
        assert selected["tablet_gate"]["selected_client_id"] == second_id
        # A stalled page must remain visibly unacknowledged after server release.
        await cdp(second, "Emulation.setScriptExecutionDisabled", {"value": True})
        started = json_request(base_url + "/api/admin/study-run/start", {})
        print("Server released the selected run.", flush=True)
        run_id = started["run_state"]["run_id"]
        assert started["run_state"]["active_client_id"] == second_id
        await asyncio.sleep(2.2)
        pending = json_request(base_url + "/api/admin/study-run")["tablet_gate"]
        assert pending["status"] == "awaiting_ack" and not pending["observed"]
        await cdp(second, "Emulation.setScriptExecutionDisabled", {"value": False})

        async def selected_started():
            visible = not await evaluate(second, "document.getElementById('screen-waiting').classList.contains('active')")
            gate = json_request(base_url + "/api/admin/study-run")["tablet_gate"]
            return visible and gate["observed"]

        await until(selected_started)
        assert await evaluate(first, "document.getElementById('screen-waiting').classList.contains('active')")
        try:
            json_request(base_url + "/api/study/session/start", {
                "client_id": first_id, "study_id": "browser-smoke", "participant_id": "wrong-page",
                "study_run_id": run_id, "require_admin_start": False,
            })
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("The unselected page bypassed the run assignment.")
        selected_session = json_request(base_url + "/api/study/session/start", {
            "client_id": second_id, "study_id": "browser-smoke", "participant_id": "p01",
            "study_run_id": run_id, "require_admin_start": True,
        })
        assert selected_session["ok"]
        print("Browser smoke passed: distinct codes, selected transition, other page waiting, acknowledgement, session start.")


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        root_path = Path(root)
        port = free_port()
        env = {**os.environ,
               "STUDY_RUNNER_DATA_DIR": str(root_path / "data"),
               "STUDY_RUNNER_USER_CONFIG_DIR": str(root_path / "user"),
               "STUDY_RUNNER_DISABLE_HARDWARE": "1",
               "STUDY_RUNNER_DISABLE_BACKGROUND": "1"}
        server_code = "from study_runner.apps.server import create_app; create_app().run(host='127.0.0.1', port=" + str(port) + ", use_reloader=False)"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        server = subprocess.Popen([sys.executable, "-c", server_code], cwd=Path(__file__).resolve().parents[2],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=creationflags)
        browser = None
        try:
            base_url = f"http://127.0.0.1:{port}"
            async def server_ready():
                try:
                    return json_request(base_url + "/api/health")
                except OSError:
                    return None
            asyncio.run(until(server_ready))
            print("Test server ready.", flush=True)
            saved = json_request(base_url + "/api/config", {
                "study_id": "browser-smoke",
                "questions": [{"type": "participant-id"}, {"type": "finish"}],
            })
            assert saved["ok"]
            print("Test study saved.", flush=True)
            profile = root_path / "browser-profile"
            browser = subprocess.Popen([
                str(browser_path()), "--headless=new", "--no-first-run", "--no-default-browser-check",
                "--remote-debugging-port=0", "--remote-allow-origins=*", f"--user-data-dir={profile}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
            async def browser_ready():
                active_port = profile / "DevToolsActivePort"
                return active_port.read_text().splitlines()[0] if active_port.is_file() else None
            debug_port = asyncio.run(until(browser_ready))
            print("Headless browser ready.", flush=True)
            asyncio.run(asyncio.wait_for(run(base_url, f"http://127.0.0.1:{debug_port}"), timeout=60))
        finally:
            if browser is not None:
                browser.terminate()
                browser.wait(timeout=10)
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
