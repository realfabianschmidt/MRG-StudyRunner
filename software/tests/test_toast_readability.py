"""Admin messages must stay readable: no translucent toast backgrounds."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ADMIN_CSS = Path(__file__).resolve().parents[1] / "study_runner" / "apps" / "ui" / "styles" / "admin.css"


class ToastReadabilityTests(unittest.TestCase):
    def test_toast_variants_use_solid_backgrounds(self) -> None:
        css = ADMIN_CSS.read_text(encoding="utf-8")
        rules = re.findall(r"\.toast--(\w+)\s*\{([^}]*)\}", css)
        self.assertEqual({name for name, _ in rules}, {"success", "error", "info", "warning"})
        for name, body in rules:
            with self.subTest(variant=name):
                self.assertNotRegex(body, r"var\(--\w+-\d+\)", "a translucent tint makes white text unreadable")
                self.assertNotIn("rgba(", body)


if __name__ == "__main__":
    unittest.main()
