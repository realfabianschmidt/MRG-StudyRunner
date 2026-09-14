"""The shipped card catalog, renderer interface and fixture coverage agree."""
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from study_runner.plugin_framework.card_catalog import card_bindings
from study_runner.plugin_framework.plugin_layout import resolve_plugin
from study_runner.runtime_core.studies import card_extension_bridge
from study_runner.runtime_core.studies.card_extension_bridge import get_card_defaults
from study_runner.plugin_framework.process_host import get_process_runtime
from study_runner.runtime_core.studies.validation import ALLOWED_QUESTION_TYPES
from tests.support.card_type_fixtures import CARD_TYPE_FIXTURES


class CardRegistryContractTests(unittest.TestCase):
    def test_every_shipped_card_has_a_golden_fixture(self):
        self.assertEqual(set(ALLOWED_QUESTION_TYPES), set(CARD_TYPE_FIXTURES))

    def test_declared_assets_export_the_renderer_contract(self):
        for question_type, (entry, contract) in card_bindings().items():
            with self.subTest(question_type=question_type):
                self.assertEqual(entry.status, "valid", entry.errors)
                path = resolve_plugin(entry.plugin_key)[0] / entry.manifest["ui"]["extensions"]["card"]
                source = path.read_text(encoding="utf-8")
                for name in ("metaByType", "configureCard", "renderStudy", "renderEditor", "collectConfig", "collectAnswer"):
                    self.assertRegex(source, rf"export (?:const|function) {name}\b")
                self.assertNotRegex(source, r"export const defaultQuestion\b")
                self.assertEqual(get_card_defaults(question_type)["type"], question_type)


    def test_defaults_rpc_is_cached_and_reuses_its_worker(self):
        entry, _contract = card_bindings()["slider"]
        runtime = get_process_runtime(entry.plugin_key)
        card_extension_bridge._defaults_cache.pop("slider", None)
        with patch.object(runtime, "request", wraps=runtime.request) as request:
            first = get_card_defaults("slider")
            first_pid = runtime.snapshot()["pid"]
            second = get_card_defaults("slider")

        self.assertEqual(first, second)
        self.assertEqual(
            [call.args[0] for call in request.call_args_list].count("card_defaults"),
            1,
        )
        self.assertEqual(runtime.snapshot()["pid"], first_pid)


    def test_choice_and_single_share_one_extension(self):
        bindings = card_bindings()
        self.assertIs(bindings["choice"][0].plugin, bindings["single"][0].plugin)

    def test_defaults_are_returned_as_independent_copies(self):
        first = get_card_defaults("multi-slider")
        first["dimensions"][0]["label"] = "changed by an editor"
        self.assertNotEqual(first, get_card_defaults("multi-slider"))

