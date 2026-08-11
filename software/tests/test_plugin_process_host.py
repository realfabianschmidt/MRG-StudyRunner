from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from study_runner.plugin_framework.plugin_api import PluginContext
from study_runner.plugin_framework.process_host import (
    ConsoleLockedError,
    MAX_CONSOLE_LINE_BYTES,
    MAX_RESTARTS,
    PROTOCOL_PREFIX,
    PluginProcessError,
    PluginProcessRuntime,
)
from study_runner.backend.routes.plugins import bp as plugins_blueprint


_DRIVER_SOURCE = r'''
import json
import sys

PREFIX = "@study-runner "

def emit(value):
    print(PREFIX + json.dumps(value, separators=(",", ":")), flush=True)

for raw in sys.stdin:
    line = raw.rstrip("\r\n")
    if line.startswith(PREFIX):
        request = json.loads(line[len(PREFIX):])
        operation = request.get("operation")
        result = {"operation": operation}
        emit({"kind": "response", "id": request.get("id"), "ok": True, "result": result})
        if operation == "shutdown":
            break
        continue
    print("ECHO:" + line, flush=True)
    print("ERR:" + line, file=sys.stderr, flush=True)
'''


class PluginProcessRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.plugin_dir = self.root / "plugins" / "fixture"
        self.plugin_dir.mkdir(parents=True)
        (self.plugin_dir / "driver.py").write_text(_DRIVER_SOURCE, encoding="utf-8")
        self.data_dir = self.root / "data"
        self.manifest = {
            "plugin_key": "fixture",
            "request_timeout_ms": 300,
            "runtime": {
                "entrypoint": "driver.py",
                "protocol": "study-runner-stdio/v1",
                "interactive_stdin": True,
            },
        }
        self.runtime = PluginProcessRuntime(self.manifest, self.plugin_dir)
        self.context = PluginContext(
            base_dir=self.root,
            data_dir=self.data_dir,
            hardware_config={"fixture": {"enabled": True}},
            local_secrets={},
            local_secrets_file=self.root / "secrets.json",
        )
        self.runtime.initialize(self.context)

    def tearDown(self) -> None:
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def test_protocol_responses_are_hidden_and_raw_lines_are_preserved(self) -> None:
        result = self.runtime.request("status")
        self.assertEqual(result, {"operation": "status"})

        self.runtime.write_console_line("Grüße = 1 + 2", study_running=False)
        lines = self._wait_for_lines("ECHO:Grüße = 1 + 2", "ERR:Grüße = 1 + 2")

        self.assertIn(("stdout", "ECHO:Grüße = 1 + 2"), lines)
        self.assertIn(("stderr", "ERR:Grüße = 1 + 2"), lines)
        self.assertFalse(any(line.startswith(PROTOCOL_PREFIX) for _source, line in lines))

    def test_console_input_contract_is_bounded_and_cannot_spoof_protocol(self) -> None:
        for value in ("with\nnewline", "nul\x00byte", PROTOCOL_PREFIX + "{}"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runtime.write_console_line(value, study_running=False)
        with self.assertRaises(ValueError):
            self.runtime.write_console_line("ü" * (MAX_CONSOLE_LINE_BYTES // 2 + 1), study_running=False)

    def test_active_study_unlock_is_scoped_to_one_run_and_transcribed(self) -> None:
        with self.assertRaises(ConsoleLockedError):
            self.runtime.write_console_line("status", study_running=True, run_id="run-a")

        transcript = self.data_dir / "private" / "fixture.jsonl"
        self.runtime.unlock_console(600, run_id="run-a")
        self.runtime.begin_intervention_transcript(
            transcript,
            run_id="run-a",
            reason="sensor diagnosis",
        )
        self.runtime.write_console_line("status", study_running=True, run_id="run-a")
        with self.assertRaises(ConsoleLockedError):
            self.runtime.write_console_line("status", study_running=True, run_id="run-b")
        self.assertTrue(self.runtime.expire_console_unlock("run-a"))

        records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[0]["kind"], "operator_intervention")
        self.assertEqual(records[0]["reason"], "sensor diagnosis")
        self.assertTrue(any(item.get("source") == "stdin" and item.get("line") == "status" for item in records))
        self.assertEqual(records[-1]["kind"], "operator_intervention_end")

    def test_expired_unlock_durably_closes_transcript(self) -> None:
        transcript = self.data_dir / "private" / "expired.jsonl"
        self.runtime.begin_intervention_transcript(
            transcript,
            run_id="run-expired",
            reason="diagnostics",
        )
        self.runtime.unlock_console(1, run_id="run-expired")
        self.runtime._unlocked_until = time.time() - 1

        snapshot = self.runtime.snapshot()

        self.assertFalse(snapshot["console_unlocked"])
        records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[-1]["kind"], "operator_intervention_end")

    def test_restart_spawn_failures_are_bounded_without_a_waiter(self) -> None:
        self.runtime.shutdown()
        self.runtime._desired_running = True
        self.runtime._restart_count = 1
        with (
            patch.object(self.runtime, "_ensure_started", side_effect=OSError("spawn failed")) as start,
            patch("study_runner.plugin_framework.process_host.time.sleep"),
        ):
            self.runtime._restart_after_exit()

        self.assertEqual(start.call_count, MAX_RESTARTS)
        self.assertEqual(self.runtime._restart_count, MAX_RESTARTS)
        self.runtime._desired_running = False

    def test_existing_process_request_does_not_spawn_when_absent(self) -> None:
        self.runtime.shutdown()
        with patch.object(self.runtime, "_ensure_started") as ensure_started:
            with self.assertRaises(PluginProcessError):
                self.runtime.request("shutdown", _start_if_needed=False)
        ensure_started.assert_not_called()

    def test_log_rotates_to_three_bounded_generations(self) -> None:
        log_path = self.data_dir / "runtime" / "plugin_logs" / "fixture.log"
        with patch("study_runner.plugin_framework.process_host.LOG_ROTATE_BYTES", 80):
            for index in range(30):
                self.runtime._append_output("stdout", f"line-{index}-" + ("x" * 30))
        self.assertTrue(log_path.is_file())
        self.assertTrue(log_path.with_suffix(".log.1").is_file())
        self.assertLessEqual(
            len(list(log_path.parent.glob("fixture.log.*"))),
            3,
        )

    def _wait_for_lines(self, *expected: str) -> set[tuple[str, str]]:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            lines = {
                (str(item.get("source")), str(item.get("line")))
                for item in self.runtime.snapshot(tail=1000)["lines"]
            }
            if all(any(line == target for _source, line in lines) for target in expected):
                return lines
            time.sleep(0.02)
        self.fail(f"Timed out waiting for output: {expected}")


class PluginConsoleRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config["DATA_DIR"] = Path(self.temp_dir.name)
        self.app.register_blueprint(plugins_blueprint)
        self.plugin_patch = patch(
            "study_runner.backend.routes.plugins.get_plugin",
            return_value=object(),
        )
        self.plugin_patch.start()
        self.runtime = MagicMock()
        self.runtime.runtime_config = {"interactive_stdin": True}
        self.runtime.snapshot.return_value = {
            "ok": True,
            "plugin_key": "fixture",
            "running": True,
            "console_unlocked": False,
            "lines": [],
        }
        self.runtime.console_unlocked_for.return_value = False

    def tearDown(self) -> None:
        self.plugin_patch.stop()
        self.temp_dir.cleanup()

    def test_console_uses_actual_loopback_and_ignores_forwarding_headers(self) -> None:
        with (
            patch("study_runner.backend.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.backend.routes.plugins._study_run_state", return_value={"status": "loaded"}),
        ):
            remote = self.app.test_client().get(
                "/api/admin/plugins/fixture/console",
                headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
                environ_overrides={"REMOTE_ADDR": "192.0.2.20"},
            )
            local = self.app.test_client().get(
                "/api/admin/plugins/fixture/console",
                headers={"X-Forwarded-For": "192.0.2.20"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.42"},
            )

        self.assertEqual(remote.status_code, 403)
        self.assertEqual(local.status_code, 200)

    def test_study_unlock_is_scoped_and_creates_private_transcript(self) -> None:
        self.runtime.unlock_console.return_value = 1234.0
        state = {"status": "running", "run_id": "study-run-abc"}
        with (
            patch("study_runner.backend.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.backend.routes.plugins._study_run_state", return_value=state),
        ):
            response = self.app.test_client().post(
                "/api/admin/plugins/fixture/console/unlock",
                json={"confirm": True, "reason": "inspect packet gaps"},
                environ_overrides={"REMOTE_ADDR": "::1"},
            )

        self.assertEqual(response.status_code, 200)
        self.runtime.unlock_console.assert_called_once_with(600, run_id="study-run-abc")
        transcript = self.runtime.begin_intervention_transcript.call_args.args[0]
        self.assertEqual(transcript.name, "fixture.jsonl")
        self.assertIn("operator_interventions", transcript.parts)
        self.assertEqual(
            self.runtime.begin_intervention_transcript.call_args.kwargs,
            {"run_id": "study-run-abc", "reason": "inspect packet gaps"},
        )

    def test_unlock_requires_confirmation_and_reason(self) -> None:
        with (
            patch("study_runner.backend.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.backend.routes.plugins._study_run_state", return_value={"status": "running", "run_id": "run"}),
        ):
            no_confirmation = self.app.test_client().post(
                "/api/admin/plugins/fixture/console/unlock",
                json={"reason": "debug"},
            )
            no_reason = self.app.test_client().post(
                "/api/admin/plugins/fixture/console/unlock",
                json={"confirm": True},
            )
        self.assertEqual(no_confirmation.status_code, 400)
        self.assertEqual(no_reason.status_code, 400)

    def test_study_unlock_fails_closed_when_transcript_is_not_writable(self) -> None:
        self.runtime.begin_intervention_transcript.side_effect = OSError("disk full")
        state = {"status": "running", "run_id": "study-run-abc"}
        with (
            patch("study_runner.backend.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.backend.routes.plugins._study_run_state", return_value=state),
        ):
            response = self.app.test_client().post(
                "/api/admin/plugins/fixture/console/unlock",
                json={"confirm": True, "reason": "inspect packet gaps"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(response.status_code, 507)
        self.runtime.unlock_console.assert_not_called()


if __name__ == "__main__":
    unittest.main()
