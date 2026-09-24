"""No card may carry participant data from one session into the next.

Every card module (shipped and the SDK template) must be free of mutable
module-level state; discovery refuses a card that is not. The JS half checks
that the stateful cards really keep their state in cards/session-state.js.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

from study_runner.plugin_framework.card_session_isolation import (
    card_module_violations,
    find_session_state_violations,
)
from study_runner.plugin_framework.plugin_catalog import discover_plugin_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CARD_PLUGINS = PROJECT_ROOT / "study_runner" / "plugins" / "cards"
CARD_TEMPLATE = PROJECT_ROOT.parent / "tools" / "plugin_templates" / "cards"


class CardSessionIsolationRuleTests(unittest.TestCase):
    def test_every_card_module_is_free_of_module_level_state(self) -> None:
        modules = [*sorted(CARD_PLUGINS.glob("*/card.js")), CARD_TEMPLATE / "card.js"]
        self.assertGreater(len(modules), 5)
        for module in modules:
            with self.subTest(card=module.parent.name):
                self.assertEqual(card_module_violations(module), [])

    def test_scanner_flags_variables_and_empty_containers(self) -> None:
        source = "\n".join(
            [
                "let _computedId = null;",
                "const _state = {};",
                "export const cache = new Map();",
                "const selected = [];",
                "export let defaultQuestion;",
                "const QUADRANTS = [{ id: 'red' }];",
                "function f() {",
                "  let local = {};",
                "}",
            ]
        )
        violations = find_session_state_violations(source)
        self.assertEqual(len(violations), 4)
        self.assertTrue(violations[0].startswith("line 1:"))

    def test_discovery_refuses_a_card_that_keeps_module_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "example_card"
            shutil.copytree(CARD_TEMPLATE, bundle, ignore=shutil.ignore_patterns("__pycache__"))
            card = bundle / "card.js"
            card.write_text(card.read_text(encoding="utf-8") + "\nconst _answers = {};\n", encoding="utf-8")
            catalog = discover_plugin_catalog(Path(tmp), package_name=f"card_isolation_{uuid.uuid4().hex}")
            entry = next(e for e in catalog.entries if e.directory == "example_card")
            self.assertEqual(entry.status, "invalid")
            self.assertIn("keeps state between participant sessions", " ".join(entry.errors))


@unittest.skipUnless(shutil.which("node"), "Node.js is only required for the card session JS test")
class CardSessionIsolationJavaScriptTests(unittest.TestCase):
    def test_stateful_cards_are_empty_after_a_session_reset(self) -> None:
        subprocess.run(
            [shutil.which("node"), str(PROJECT_ROOT / "tests" / "js" / "card-session-isolation.test.mjs")],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
