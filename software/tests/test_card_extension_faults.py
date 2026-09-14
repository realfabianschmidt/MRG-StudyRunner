"""End-to-end fault-taxonomy and acceptance coverage for card extensions.

Package 5g.B5 made every question type its own process-isolated extension.
Existing coverage already proves the mechanism itself works: hang/timeout and
bounded restart in test_plugin_process_host.py, and the B1 golden fixtures
round-tripping through real subprocesses in test_card_type_fixtures.py /
test_card_registry_contract.py. What none of that reaches is the SAME fault
taxonomy proven through the real HTTP surface -- the global
ValidationError/CardExtensionUnavailableError handlers in
apps/server/routes/__init__.py, not just PluginProcessRuntime.request()
directly -- plus two things Codex's completion plan named explicitly:

  - proof an unrelated card and a plain server request stay usable while one
    card's process is actively failing;
  - the package's own acceptance criterion: a brand-new card type added
    through nothing but its own extension directory, with no core-code
    change, working end to end through a real child process.

"Do not claim fault isolation based only on mocks": every scenario below runs
a real Python child process under PluginProcessRuntime; nothing here mocks
the process boundary itself.

The fixture card is registered the same CONTRIBUTING.md-compliant way
test_fixture_plugin_blueprint.py registers a synthetic sensor: through
FixturePluginRootMixin's STUDY_RUNNER_TEST_EXTRA_PLUGIN_ROOT/_PACKAGE
environment seam (read only from the environment, never a request or
manifest value), merged with the real catalog for the duration of one test.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from support.fixture_plugin import FixturePluginRootMixin, write_driver_py

from study_runner.plugin_framework import registry
from study_runner.plugin_framework.plugin_catalog import PluginCatalog, discover_plugin_catalog
from study_runner.plugin_framework.process_host import STARTUP_TIMEOUT_MS, get_process_runtime


PLUGIN_KEY = "fixture_fault_card"
QUESTION_TYPE = "fixture-fault-card"

# The handler itself decides how it misbehaves from `_fault` embedded in the
# question data / answer it receives -- one fixture drives every scenario.
_PLUGIN_PY_SOURCE = f'''
import os
from study_runner.contracts.plugin_api import Plugin
from study_runner.contracts.card_validation_primitives import CardValidationError

# Module import happens exactly once, at child-process startup, before the
# driver ever reads a request off stdin -- so this marker's existence is
# proof a worker process for this plugin was actually spawned.
_marker = os.environ.get("FIXTURE_CARD_STARTED_MARKER")
if _marker:
    with open(_marker, "a", encoding="utf-8") as handle:
        handle.write("started\\n")


def get_card_defaults(question_type):
    return {{"type": question_type, "prompt": ""}}


def normalize_card_config(question_type, question_data, question_index, host_data):
    fault = question_data.get("_fault")
    if fault == "invalid":
        raise CardValidationError(f"Question {{question_index}} fixture fault: invalid input")
    if fault == "boom":
        raise RuntimeError("fixture handler exploded")
    if fault == "hang":
        blocked = os.environ.get("FIXTURE_CARD_BLOCKED_MARKER")
        if blocked:
            with open(blocked, "w", encoding="utf-8") as handle:
                handle.write("blocked")
        import time
        time.sleep(60)
    if fault == "exit":
        os._exit(17)
    return {{"type": question_type, "prompt": str(question_data.get("prompt") or "")}}


def validate_card_answer(question_type, question, answer, question_number):
    if answer == "_fault_invalid":
        raise CardValidationError(f"Question {{question_number}} fixture fault: invalid answer")
    if answer == "_fault_boom":
        raise RuntimeError("fixture handler exploded")
    return answer


PLUGIN = Plugin(
    key={PLUGIN_KEY!r},
    label="Fixture Fault Card",
    category="card",
    config_key={PLUGIN_KEY!r},
    can_toggle=False,
    get_card_defaults=get_card_defaults,
    normalize_card_config=normalize_card_config,
    validate_card_answer=validate_card_answer,
)
'''


class _FixtureFaultCardMixin(FixturePluginRootMixin):
    """Shared setup: one real card extension, merged with the real catalog."""

    fixture_group_name = "fixture-fault-card"

    def setUp(self) -> None:
        super().setUp()
        self.marker_path = self.root / "fixture-card-started.log"
        self.blocked_marker_path = self.root / "fixture-card-blocked.log"
        self._previous_marker_env = os.environ.get("FIXTURE_CARD_STARTED_MARKER")
        self._previous_blocked_env = os.environ.get("FIXTURE_CARD_BLOCKED_MARKER")
        os.environ["FIXTURE_CARD_STARTED_MARKER"] = str(self.marker_path)
        os.environ["FIXTURE_CARD_BLOCKED_MARKER"] = str(self.blocked_marker_path)
        self._write_fixture_card()

    def tearDown(self) -> None:
        # Do not reset all runtimes: that would wipe shipped plugins for the
        # rest of the suite. The catalog patches installed by each test restore
        # the original objects when their ExitStack closes, so reloading here
        # would instead rediscover this temporary root just before it vanishes.
        fixture_runtime = get_process_runtime(PLUGIN_KEY)
        if fixture_runtime is not None:
            fixture_runtime.shutdown()
        if self._previous_marker_env is None:
            os.environ.pop("FIXTURE_CARD_STARTED_MARKER", None)
        else:
            os.environ["FIXTURE_CARD_STARTED_MARKER"] = self._previous_marker_env
        if self._previous_blocked_env is None:
            os.environ.pop("FIXTURE_CARD_BLOCKED_MARKER", None)
        else:
            os.environ["FIXTURE_CARD_BLOCKED_MARKER"] = self._previous_blocked_env
        super().tearDown()
        self.assertIsNone(registry.get_plugin(PLUGIN_KEY), "fixture card leaked globally")

    def _write_fixture_card(self) -> None:
        plugin_dir = self.package_dir / PLUGIN_KEY
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
        manifest = {
            "api_version": 5,
            "plugin_key": PLUGIN_KEY,
            "config_key": PLUGIN_KEY,
            "version": "1.0.0",
            "category": "card",
            "capabilities": {
                "card_contract": {
                    "version": 1,
                    "question_types": [QUESTION_TYPE],
                    "answerless_types": [],
                    "host_data": [],
                }
            },
            "runtime": {
                "entrypoint": "driver.py",
                "protocol": "study-runner-stdio/v1",
                "can_toggle": False,
                # Generous: the "exit" fault must lose the race against the
                # process-exit detector, not against a plain timeout, or a
                # 503 here would be proving the wrong thing.
                "operation_timeouts_ms": {
                    "card_defaults": 2_000,
                    "card_normalize": 2_000,
                    "card_validate_answer": 2_000,
                },
            },
            "ui": {
                "label": "Fixture Fault Card",
                "order": 9_999,
                "extensions": {"card": "card.js"},
                "assets": ["card.js"],
            },
        }
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (plugin_dir / "plugin.py").write_text(_PLUGIN_PY_SOURCE, encoding="utf-8")
        (plugin_dir / "card.js").write_text(
            "export const metaByType = {'fixture-fault-card': {type: 'fixture-fault-card', "
            "icon: 'puzzle', label: 'Fixture', pill: 'pill-text'}};\n"
            "export function configureCard(){}\n"
            "export function renderStudy(){return '';}\n"
            "export function renderEditor(){return '';}\n"
            "export function collectConfig(){return {};}\n"
            "export function collectAnswer(){return null;}\n",
            encoding="utf-8",
        )
        write_driver_py(plugin_dir, PLUGIN_KEY)

    def _merged_catalog(self) -> PluginCatalog:
        fixture_catalog = discover_plugin_catalog(self.package_dir, package_name=self.package_name)
        self.assertEqual([plugin.key for plugin in fixture_catalog.plugins], [PLUGIN_KEY])
        self.assertFalse(fixture_catalog.invalid_entries, fixture_catalog.invalid_entries)
        original_catalog = registry.get_plugin_catalog()
        return PluginCatalog(entries=(*original_catalog.entries, *fixture_catalog.entries))

    def _install_catalog(self, stack: ExitStack, catalog: PluginCatalog) -> None:
        stack.enter_context(patch.object(registry, "_PLUGIN_CATALOG", catalog))
        stack.enter_context(patch.object(registry, "PLUGINS", catalog.plugins))
        stack.enter_context(
            patch.object(registry, "PLUGINS_BY_KEY", {plugin.key: plugin for plugin in catalog.plugins})
        )

    def _make_app(self, data_dir: str):
        from study_runner.apps.server import create_app

        env = {
            "STUDY_RUNNER_DATA_DIR": data_dir,
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            return create_app()

    def _save_config(self, client, *questions) -> "Response":  # noqa: F821 - flask Response
        return client.post(
            "/api/config",
            json={"study_id": "fixture-fault-study", "questions": list(questions)},
        )

    def has_started(self) -> bool:
        return self.marker_path.exists()


class CardFaultHTTPMappingTests(_FixtureFaultCardMixin, unittest.TestCase):
    """The 400-vs-503 split, proven through a real HTTP round trip.

    Package 5g.B5's own plan requires this distinction: HTTP 400 for invalid
    input, HTTP 503 (with a distinct error code) for the card worker being
    unavailable -- and never a crash or a silently-accepted save either way.
    """

    def test_invalid_input_is_http_400_with_invalid_input_error_code(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()
                response = self._save_config(
                    client, {"type": QUESTION_TYPE, "_fault": "invalid"}
                )
        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["error_code"], "invalid_input")

    def test_handler_exception_is_http_503_with_card_unavailable_error_code(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()
                response = self._save_config(
                    client, {"type": QUESTION_TYPE, "_fault": "boom"}
                )
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["error_code"], "card_unavailable")

    def test_process_exit_mid_call_is_http_503_not_a_hang_or_a_500(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()
                response = self._save_config(
                    client, {"type": QUESTION_TYPE, "_fault": "exit"}
                )
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["error_code"], "card_unavailable")

    def test_well_formed_input_still_saves_normally(self) -> None:
        """The fixture is not just a fault switch -- it has an honest happy path."""
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()
                response = self._save_config(
                    client, {"type": QUESTION_TYPE, "prompt": "How loud was that?"}
                )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))


class CardAnswerSubmissionRecoveryTests(_FixtureFaultCardMixin, unittest.TestCase):
    def test_invalid_answer_preserves_recovery_and_a_valid_retry_can_commit(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()
                saved = self._save_config(
                    client,
                    {"type": QUESTION_TYPE, "prompt": "How was it?"},
                )
                self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
                submission = {
                    "session_id": "fixture-answer-retry",
                    "study_id": "fixture-fault-study",
                    "participant_id": "p01",
                    "timestamp_start": "2026-09-09T10:00:00Z",
                    "timestamp_end": "2026-09-09T10:01:00Z",
                    "answers": {"q0": "_fault_invalid"},
                    "participant_metadata": {},
                    "answer_events": [],
                    "card_events": [],
                }

                failed = client.post("/api/results", json=submission)
                self.assertEqual(failed.status_code, 400, failed.get_data(as_text=True))
                self.assertEqual(failed.get_json()["error_code"], "invalid_input")
                recovery_files = list(Path(data_dir).rglob("_recovery/*.json"))
                self.assertEqual(len(recovery_files), 1)
                self.assertEqual(json.loads(recovery_files[0].read_text(encoding="utf-8")), submission)

                retried = client.post(
                    "/api/results",
                    json={**submission, "answers": {"q0": "valid answer"}},
                )
                self.assertEqual(retried.status_code, 202, retried.get_data(as_text=True))
                self.assertTrue(retried.get_json()["accepted"])
                self.assertTrue(recovery_files[0].is_file(), "failed raw submission must remain recoverable")


class UnrelatedWorkStaysAvailableDuringACardFaultTests(_FixtureFaultCardMixin, unittest.TestCase):
    """A card crashing must not take anything else down with it."""

    def test_a_different_card_and_a_plain_route_stay_usable_after_the_fault(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()

                faulted = self._save_config(client, {"type": QUESTION_TYPE, "_fault": "boom"})
                self.assertEqual(faulted.status_code, 503)

                # A shipped, unrelated card type: its own process was never
                # touched by the fixture's fault.
                healthy = self._save_config(
                    client,
                    {"type": "participant-id"},
                    {"type": "text", "prompt": "Anything else?"},
                    {"type": "finish"},
                )
                self.assertEqual(healthy.status_code, 200, healthy.get_data(as_text=True))

                # And the server itself is not wedged by one card's failure.
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/api/config").status_code, 200)


    def test_plain_http_and_another_card_work_while_handler_is_blocked(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        self._save_config,
                        app.test_client(),
                        {"type": QUESTION_TYPE, "_fault": "hang"},
                    )
                    # Getting here means spawning a driver process, so allow the
                    # framework's own startup budget plus a margin. A shorter,
                    # hand-picked wait was really an assumption about how many
                    # plugins ship -- adding one more folder to discovery was
                    # enough to break it, which says nothing about the fault
                    # isolation this test is actually about.
                    deadline = time.monotonic() + (STARTUP_TIMEOUT_MS / 1000.0) * 3
                    while time.monotonic() < deadline and not self.blocked_marker_path.is_file():
                        time.sleep(0.01)
                    self.assertTrue(self.blocked_marker_path.is_file(), "fixture handler never blocked")

                    client = app.test_client()
                    started = time.monotonic()
                    healthy = self._save_config(
                        client,
                        {"type": "participant-id"},
                        {"type": "text", "prompt": "Still available?"},
                        {"type": "finish"},
                    )
                    self.assertEqual(healthy.status_code, 200, healthy.get_data(as_text=True))
                    self.assertEqual(client.get("/").status_code, 200)
                    self.assertLess(time.monotonic() - started, 1.5)

                    # The first call of an operation includes the five-second driver
                    # startup allowance; unrelated requests above must still finish early.
                    faulted = future.result(timeout=8)
                    self.assertEqual(faulted.status_code, 503, faulted.get_data(as_text=True))


class CardWorkersStayLazyTests(_FixtureFaultCardMixin, unittest.TestCase):
    """Cards must not start, or receive secrets, before a study needs them."""

    def test_startup_and_status_polling_spawn_no_card_worker(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/admin").status_code, 200)
                self.assertEqual(client.get("/api/plugins/catalog").status_code, 200)
                self.assertEqual(client.get("/api/admin/status").status_code, 200)
                self.assertEqual(client.get("/api/config").status_code, 200)
        self.assertFalse(
            self.has_started(),
            "a card worker process was spawned without any study using its type",
        )

    def test_the_worker_starts_only_once_the_type_is_actually_used(self) -> None:
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                app = self._make_app(data_dir)
                client = app.test_client()
                self.assertFalse(self.has_started())
                response = self._save_config(client, {"type": QUESTION_TYPE, "prompt": "x"})
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(self.has_started(), "the fixture card never actually started")

    def test_no_hardware_config_or_secrets_reach_a_card_worker(self) -> None:
        """Cards get no app context at all -- see PluginProcessRuntime.is_card."""
        from study_runner.plugin_framework.process_host import get_process_runtime

        secret_marker = "SECRET-fixture-should-never-leak-jK3q"
        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)
            with tempfile.TemporaryDirectory() as data_dir:
                secrets_path = Path(data_dir) / "local-secrets.json"
                secrets_path.write_text(
                    json.dumps({"fixture_fault_card": {"token": secret_marker}}),
                    encoding="utf-8",
                )
                app = self._make_app(data_dir)
                app.config["LOCAL_SECRETS"] = {"fixture_fault_card": {"token": secret_marker}}
                app.config["HARDWARE_CONFIG"] = {"fixture_fault_card": {"token": secret_marker}}
                client = app.test_client()

                # The proxy object is registered eagerly (cheap, no process);
                # only _ensure_started() actually spawns a subprocess.
                runtime = get_process_runtime(PLUGIN_KEY)
                self.assertIsNotNone(runtime)
                self.assertIsNone(runtime._process, "must still be lazy before first use")

                response = self._save_config(client, {"type": QUESTION_TYPE, "prompt": "x"})
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertIsNotNone(runtime._process)
                sent_lines: list[str] = []
                original_write_raw = runtime._write_raw

                def _recording_write_raw(value, **kwargs):
                    sent_lines.append(value)
                    return original_write_raw(value, **kwargs)

                with patch.object(runtime, "_write_raw", side_effect=_recording_write_raw):
                    second = self._save_config(client, {"type": QUESTION_TYPE, "prompt": "y"})
                self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        self.assertTrue(sent_lines, "the recording wrapper never observed a real request")
        for line in sent_lines:
            self.assertNotIn(secret_marker, line)
            self.assertNotIn("_context", line)


class SyntheticCardAddedThroughItsOwnDirectoryTests(_FixtureFaultCardMixin, unittest.TestCase):
    """Package 5g.B5's own acceptance criterion, made executable.

    A new card type works end to end -- defaults, normalize, validate,
    through a real child process -- from nothing but its own extension
    directory, discovered the same test-only environment-seam way
    test_fixture_plugin_blueprint.py adds a synthetic sensor. No core file is
    touched to make this work, which the closing assertion checks directly,
    the same way that blueprint test does for its plugin key.
    """

    def test_defaults_normalize_and_validate_all_work_through_a_real_process(self) -> None:
        from study_runner.runtime_core.studies.card_extension_bridge import (
            get_card_defaults,
            normalize_card_config,
            validate_card_answer,
        )

        catalog = self._merged_catalog()
        with ExitStack() as stack:
            self._install_catalog(stack, catalog)

            defaults = get_card_defaults(QUESTION_TYPE)
            self.assertEqual(defaults, {"type": QUESTION_TYPE, "prompt": ""})

            normalized = normalize_card_config(
                QUESTION_TYPE, {"prompt": "How was it?"}, 1
            )
            self.assertEqual(normalized, {"type": QUESTION_TYPE, "prompt": "How was it?"})

            answer = validate_card_answer(QUESTION_TYPE, normalized, "fine", 1)
            self.assertEqual(answer, "fine")

    def test_the_new_plugin_key_appears_in_no_core_file(self) -> None:
        for relative_path in (
            "study_runner/plugin_framework/card_catalog.py",
            "study_runner/runtime_core/studies/validation.py",
            "study_runner/runtime_core/studies/card_extension_bridge.py",
            "study_runner/apps/ui/scripts/cards/index.js",
        ):
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn(PLUGIN_KEY, source, relative_path)
            self.assertNotIn(QUESTION_TYPE, source, relative_path)


if __name__ == "__main__":
    unittest.main()
