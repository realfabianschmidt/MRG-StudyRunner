from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SessionsBrowserContractTests(unittest.TestCase):
    def test_browser_selects_canonical_session_folder_for_detail_and_signals(self) -> None:
        source = (
            PROJECT_ROOT / "study_runner" / "frontend" / "scripts" / "admin" / "sessions-browser.js"
        ).read_text(encoding="utf-8")

        self.assertIn('data-session-folder="${escapeHtml(session.session_folder)}"', source)
        self.assertIn("params.set('session_folder', sessionFolder)", source)
        # The signals request carries the same canonical folder. Asserted on the
        # parameter rather than on one way of building the query string, so
        # swapping a template literal for URLSearchParams is not a regression.
        self.assertIn("session_folder: session.session_folder", source)
        self.assertNotIn("result_file=", source)


if __name__ == "__main__":
    unittest.main()
