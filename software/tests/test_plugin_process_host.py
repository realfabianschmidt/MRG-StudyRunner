from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from study_runner.contracts.plugin_api import PluginContext
from study_runner.plugin_framework.process_host import (
    ConsoleLockedError,
    MAX_CONSOLE_LINE_BYTES,
    MAX_RESTARTS,
    PROTOCOL_PREFIX,
    PluginProcessError,
    PluginProcessRuntime,
    _validate_response_envelope,
)
from study_runner.apps.server.routes.plugins import bp as plugins_blueprint


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


_HANGING_DRIVER_SOURCE = r'''
import json
import sys
import time

PREFIX = "@study-runner "

def emit(value):
    print(PREFIX + json.dumps(value, separators=(",", ":")), flush=True)

for raw in sys.stdin:
    line = raw.rstrip("\r\n")
    if not line.startswith(PREFIX):
        continue
    request = json.loads(line[len(PREFIX):])
    operation = request.get("operation")
    if operation == "hang":
        time.sleep(60)  # Actually blocks the driver loop until the host terminates it.
    emit({"kind": "response", "id": request.get("id"), "ok": True, "result": {"operation": operation}})
    if operation == "shutdown":
        break
'''


class TimeoutTerminatesTheHungProcessTests(unittest.TestCase):
    """Package 5g.B5: a call that times out must not wedge its plugin forever.

    Before this fix, `PluginProcessRuntime.request()` raised on timeout and
    left the process running untouched -- only a real process *exit*
    triggered the existing bounded auto-restart. A driver stuck on one
    operation therefore silently blocked every future call against that
    same plugin key for the rest of the run, with no self-healing.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.hanging_dir = self.root / "plugins" / "hangs"
        self.hanging_dir.mkdir(parents=True)
        (self.hanging_dir / "driver.py").write_text(_HANGING_DRIVER_SOURCE, encoding="utf-8")
        self.other_dir = self.root / "plugins" / "unrelated"
        self.other_dir.mkdir(parents=True)
        (self.other_dir / "driver.py").write_text(_DRIVER_SOURCE, encoding="utf-8")
        self.data_dir = self.root / "data"

        self.hanging_manifest = {
            "plugin_key": "hangs",
            "capabilities": ["card_contract"],
            "request_timeout_ms": 200,
            "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1"},
        }
        self.hanging = PluginProcessRuntime(self.hanging_manifest, self.hanging_dir)
        self.other = PluginProcessRuntime(
            {
                "plugin_key": "unrelated",
                "request_timeout_ms": 300,
                "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1"},
            },
            self.other_dir,
        )
        context = PluginContext(
            base_dir=self.root,
            data_dir=self.data_dir,
            hardware_config={},
            local_secrets={},
            local_secrets_file=self.root / "secrets.json",
        )
        self.hanging.initialize(context)
        self.hanging.request("status", timeout_ms=1000)
        self.other.initialize(context)

    def tearDown(self) -> None:
        self.hanging.shutdown()
        self.other.shutdown()
        self.temp_dir.cleanup()

    def test_a_timed_out_call_fails_promptly_with_a_clear_error(self) -> None:
        with self.assertRaisesRegex(PluginProcessError, "timed out during hang"):
            self.hanging.request("hang", timeout_ms=150)

    def test_the_hung_process_is_terminated_so_it_can_be_recovered(self) -> None:
        original_pid = self.hanging._process.pid
        with self.assertRaises(PluginProcessError):
            self.hanging.request("hang", timeout_ms=150)

        # _wait_for_exit's own thread races this assertion; give it a brief,
        # bounded window to observe the exit and clear self._process.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with self.hanging._lock:
                process = self.hanging._process
            if process is None or process.pid != original_pid:
                break
            time.sleep(0.02)
        else:
            self.fail("Timed-out driver process was never terminated.")

    def test_the_same_plugin_recovers_through_the_existing_auto_restart(self) -> None:
        with self.assertRaises(PluginProcessError):
            self.hanging.request("hang", timeout_ms=150)
        # The real backoff delay before the first restart attempt is fine to
        # wait out here: it is capped low (min(4.0, 0.5)) and this proves the
        # *existing* restart path end-to-end, not a mocked shortcut.
        deadline = time.monotonic() + 5.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                result = self.hanging.request("status")
                self.assertEqual(result, {"operation": "status"})
                return
            except PluginProcessError as error:
                last_error = error
                time.sleep(0.05)
        self.fail(f"Plugin never recovered after the timeout: {last_error}")

    def test_an_unrelated_plugin_stays_usable_while_the_other_is_hung(self) -> None:
        with self.assertRaises(PluginProcessError):
            self.hanging.request("hang", timeout_ms=150)
        # The unrelated plugin's own process was never touched.
        result = self.other.request("status")
        self.assertEqual(result, {"operation": "status"})


    def test_timeout_gate_rejects_calls_before_the_old_process_exits(self) -> None:
        process = self.hanging._process
        self.assertIsNotNone(process)
        self.hanging._recovering = True
        with self.assertRaisesRegex(PluginProcessError, "is recovering"):
            self.hanging._ensure_started()
        self.assertIs(self.hanging._process, process)

    def test_response_envelope_rejects_unknown_error_kinds(self) -> None:
        with self.assertRaisesRegex(PluginProcessError, "malformed response"):
            _validate_response_envelope(
                {
                    "kind": "response",
                    "id": "request-1",
                    "ok": False,
                    "error": "bad",
                    "error_kind": "mystery",
                },
                request_id="request-1",
                plugin_key="hangs",
            )


class SensorAndUploadTimeoutBehaviorIsUnchangedTests(unittest.TestCase):
    """Package 5g.B5 added is_card-gated termination and a permanent
    post-MAX_RESTARTS lockout for cards. Neither is new behavior for a
    sensor/destination/output plugin -- a manifest with no card_contract
    capability must keep exactly its pre-5g.B5 timeout behavior: the process
    is left running (never terminated), and it is never locked out no matter
    how many times it times out, because self-healing there has always come
    from the next on-demand call's unconditional _ensure_started(), not from
    a restart budget.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.plugin_dir = self.root / "plugins" / "hangs"
        self.plugin_dir.mkdir(parents=True)
        (self.plugin_dir / "driver.py").write_text(_HANGING_DRIVER_SOURCE, encoding="utf-8")
        self.runtime = PluginProcessRuntime(
            {
                "plugin_key": "hangs",
                # Deliberately no "capabilities" at all -- a sensor/upload
                # plugin, not a card.
                "request_timeout_ms": 200,
                "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1"},
            },
            self.plugin_dir,
        )
        context = PluginContext(
            base_dir=self.root,
            data_dir=self.root / "data",
            hardware_config={},
            local_secrets={},
            local_secrets_file=self.root / "secrets.json",
        )
        self.runtime.initialize(context)
        self.runtime.request("status", timeout_ms=1000)

    def tearDown(self) -> None:
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def test_a_timed_out_non_card_process_is_left_running(self) -> None:
        self.assertFalse(self.runtime.is_card)
        original_pid = self.runtime._process.pid
        with self.assertRaisesRegex(PluginProcessError, "timed out during hang"):
            self.runtime.request("hang", timeout_ms=150)
        self.assertEqual(self.runtime._process.pid, original_pid)

    def test_repeated_timeouts_never_produce_a_card_style_lockout(self) -> None:
        for _ in range(MAX_RESTARTS + 3):
            with self.assertRaisesRegex(PluginProcessError, "timed out during hang") as caught:
                self.runtime.request("hang", timeout_ms=150)
            self.assertNotIn("exhausted recovery attempts", str(caught.exception))
            self.assertNotIn("is recovering", str(caught.exception))


class RestartBudgetResetsOnRecoveryTests(unittest.TestCase):
    """A card's MAX_RESTARTS budget must bound a crash *loop*, not accumulate
    across incidents that each recovered cleanly.

    Only cards get a permanent post-MAX_RESTARTS lockout (_ensure_started's
    is_card branch) -- every other plugin type just stops *passive*
    auto-restart-on-exit and still self-heals on the next on-demand call.
    Without a reset, three unrelated crashes spread over a long run would
    silently strand an otherwise-healthy card until the whole app restarts.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.plugin_dir = self.root / "plugins" / "hangs"
        self.plugin_dir.mkdir(parents=True)
        (self.plugin_dir / "driver.py").write_text(_HANGING_DRIVER_SOURCE, encoding="utf-8")
        self.runtime = PluginProcessRuntime(
            {
                "plugin_key": "hangs",
                "capabilities": ["card_contract"],
                "request_timeout_ms": 200,
                "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1"},
            },
            self.plugin_dir,
        )
        context = PluginContext(
            base_dir=self.root,
            data_dir=self.root / "data",
            hardware_config={},
            local_secrets={},
            local_secrets_file=self.root / "secrets.json",
        )
        self.runtime.initialize(context)
        self.runtime.request("status", timeout_ms=1000)

    def tearDown(self) -> None:
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def _crash_and_wait_for_recovery(self) -> None:
        with self.assertRaises(PluginProcessError):
            self.runtime.request("hang", timeout_ms=150)
        deadline = time.monotonic() + 5.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                result = self.runtime.request("status")
                self.assertEqual(result, {"operation": "status"})
                return
            except PluginProcessError as error:
                last_error = error
                time.sleep(0.05)
        self.fail(f"Plugin never recovered after the timeout: {last_error}")

    def test_restart_count_resets_after_each_successful_recovery(self) -> None:
        for _ in range(MAX_RESTARTS + 2):
            self._crash_and_wait_for_recovery()
            self.assertEqual(
                self.runtime._restart_count,
                0,
                "a successful request after recovery must clear the crash "
                "counter, or incidents that each recovered cleanly would "
                "eventually exhaust MAX_RESTARTS and strand a healthy card",
            )

    def test_a_genuine_crash_loop_still_exhausts_recovery(self) -> None:
        """The bound survives: consecutive crashes with no successful request
        landing between them -- the actual failure mode MAX_RESTARTS exists
        for -- still lock the card out until the app itself restarts."""
        deadline = time.monotonic() + 15.0
        locked_out = False
        while time.monotonic() < deadline:
            try:
                self.runtime.request("hang", timeout_ms=150)
            except PluginProcessError as error:
                if "exhausted recovery attempts" in str(error):
                    locked_out = True
                    break
            time.sleep(0.05)
        self.assertTrue(locked_out, "a genuine crash loop must still exhaust recovery")
        self.assertGreaterEqual(self.runtime._restart_count, MAX_RESTARTS)


_MALFORMED_RESPONSE_DRIVER_SOURCE = r'''
import json
import sys

PREFIX = "@study-runner "

def emit(value):
    print(PREFIX + json.dumps(value, separators=(",", ":")), flush=True)

for raw in sys.stdin:
    line = raw.rstrip("\r\n")
    if not line.startswith(PREFIX):
        continue
    request = json.loads(line[len(PREFIX):])
    operation = request.get("operation")
    if operation == "malformed":
        # ok=True but no "result" key at all: a driver bug, not a raised
        # error -- request() must not paper over this with an implicit None.
        emit({"kind": "response", "id": request.get("id"), "ok": True})
        continue
    emit({"kind": "response", "id": request.get("id"), "ok": True, "result": {"operation": operation}})
    if operation == "shutdown":
        break
'''


class MalformedResponseTests(unittest.TestCase):
    """A response missing "result" is a driver bug, not a value to trust."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.plugin_dir = self.root / "plugins" / "malformed"
        self.plugin_dir.mkdir(parents=True)
        (self.plugin_dir / "driver.py").write_text(_MALFORMED_RESPONSE_DRIVER_SOURCE, encoding="utf-8")
        self.runtime = PluginProcessRuntime(
            {
                "plugin_key": "malformed",
                "request_timeout_ms": 1000,
                "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1"},
            },
            self.plugin_dir,
        )
        context = PluginContext(
            base_dir=self.root,
            data_dir=self.root / "data",
            hardware_config={},
            local_secrets={},
            local_secrets_file=self.root / "secrets.json",
        )
        self.runtime.initialize(context)

    def tearDown(self) -> None:
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def test_a_response_missing_result_raises_instead_of_returning_none_silently(self) -> None:
        with self.assertRaisesRegex(PluginProcessError, "malformed response"):
            self.runtime.request("malformed")

    def test_the_process_stays_usable_for_the_next_well_formed_call(self) -> None:
        with self.assertRaises(PluginProcessError):
            self.runtime.request("malformed")
        result = self.runtime.request("status")
        self.assertEqual(result, {"operation": "status"})


_SLOW_THEN_RESPOND_DRIVER_SOURCE = r'''
import json
import sys
import time

PREFIX = "@study-runner "

def emit(value):
    print(PREFIX + json.dumps(value, separators=(",", ":")), flush=True)

for raw in sys.stdin:
    line = raw.rstrip("\r\n")
    if not line.startswith(PREFIX):
        continue
    request = json.loads(line[len(PREFIX):])
    operation = request.get("operation")
    if operation == "slow":
        time.sleep(0.6)  # Longer than the test's timeout, short enough to bound the test.
    emit({"kind": "response", "id": request.get("id"), "ok": True, "result": {"operation": operation}})
    if operation == "shutdown":
        break
'''


class LateResponseCannotResurrectATimedOutCallTests(unittest.TestCase):
    """A response that arrives after request() already raised on timeout must
    be silently dropped, not mistaken for a fresh call's answer.

    Only a non-card plugin can prove this with a real process end to end:
    cards terminate their process on timeout by design (is_card in
    _terminate_after_timeout), which normally kills the child before it ever
    gets to emit the late reply this test needs to actually observe arriving.
    request()'s own pop-before-terminate ordering is what this protects.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.plugin_dir = self.root / "plugins" / "slow"
        self.plugin_dir.mkdir(parents=True)
        (self.plugin_dir / "driver.py").write_text(_SLOW_THEN_RESPOND_DRIVER_SOURCE, encoding="utf-8")
        self.runtime = PluginProcessRuntime(
            {
                "plugin_key": "slow",
                "request_timeout_ms": 300,
                "runtime": {"entrypoint": "driver.py", "protocol": "study-runner-stdio/v1"},
            },
            self.plugin_dir,
        )
        context = PluginContext(
            base_dir=self.root,
            data_dir=self.root / "data",
            hardware_config={},
            local_secrets={},
            local_secrets_file=self.root / "secrets.json",
        )
        self.runtime.initialize(context)

    def tearDown(self) -> None:
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def test_late_response_is_dropped_and_does_not_taint_the_next_call(self) -> None:
        original_pid = self.runtime._process.pid
        with self.assertRaises(PluginProcessError):
            self.runtime.request("slow", timeout_ms=150)

        # Confirms the premise: a non-card plugin is never terminated on
        # timeout, so this is still the same process, still mid-sleep, about
        # to emit its late "slow" reply on this same stdout stream.
        self.assertEqual(self.runtime._process.pid, original_pid)

        # Outlive the driver's 0.6s sleep so its late response is actually
        # written and read before the next call is made.
        time.sleep(0.9)

        result = self.runtime.request("status")
        self.assertEqual(result, {"operation": "status"})


class PackagedPluginProcessTests(unittest.TestCase):
    def test_frozen_launch_needs_neither_source_root_markers_nor_driver_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary).resolve() / "_internal"
            plugin_dir = bundle / "study_runner" / "plugins" / "fixture"
            plugin_dir.mkdir(parents=True)
            executable = str(bundle.parent / "study-runner-server.exe")
            runtime = PluginProcessRuntime({"plugin_key": "fixture"}, plugin_dir)
            process = MagicMock()
            process.poll.return_value = None
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", str(bundle), create=True),
                patch.object(sys, "executable", executable),
                patch("study_runner.plugin_framework.process_host.subprocess.Popen", return_value=process) as spawn,
                patch("study_runner.plugin_framework.process_host.threading.Thread.start"),
            ):
                runtime._ensure_started()

            self.assertFalse((bundle / "server.py").exists())
            self.assertFalse((plugin_dir / "driver.py").exists())
            self.assertEqual(spawn.call_args.args[0], [executable, "--plugin-driver", "fixture"])
            self.assertEqual(spawn.call_args.kwargs["env"]["PYTHONPATH"].split(os.pathsep)[0], str(bundle))
            self.assertEqual(spawn.call_args.kwargs["cwd"], str(plugin_dir))

    def test_source_launch_still_rejects_a_missing_driver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(sys, "frozen", False, create=True):
            runtime = PluginProcessRuntime({"plugin_key": "fixture"}, Path(temporary))
            with self.assertRaisesRegex(PluginProcessError, "entrypoint is missing"):
                runtime._command()

    def test_entrypoint_cannot_escape_the_plugin_directory_in_either_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = PluginProcessRuntime(
                {"plugin_key": "fixture", "runtime": {"entrypoint": "../driver.py"}},
                Path(temporary),
            )
            for frozen in (False, True):
                with self.subTest(frozen=frozen), patch.object(sys, "frozen", frozen, create=True):
                    with self.assertRaisesRegex(PluginProcessError, "escapes its directory"):
                        runtime._command()


class PluginConsoleRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config["DATA_DIR"] = Path(self.temp_dir.name)
        self.app.register_blueprint(plugins_blueprint)
        self.plugin_patch = patch(
            "study_runner.apps.server.routes.plugins.get_plugin",
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
            patch("study_runner.apps.server.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.apps.server.routes.plugins._study_run_state", return_value={"status": "loaded"}),
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
            patch("study_runner.apps.server.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.apps.server.routes.plugins._study_run_state", return_value=state),
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
            patch("study_runner.apps.server.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.apps.server.routes.plugins._study_run_state", return_value={"status": "running", "run_id": "run"}),
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
            patch("study_runner.apps.server.routes.plugins.get_process_runtime", return_value=self.runtime),
            patch("study_runner.apps.server.routes.plugins._study_run_state", return_value=state),
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
