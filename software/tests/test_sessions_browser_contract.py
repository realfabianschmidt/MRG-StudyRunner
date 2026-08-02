from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SessionsBrowserContractTests(unittest.TestCase):
    def test_browser_selects_canonical_session_folder_for_detail_and_signals(self) -> None:
        source = (
            PROJECT_ROOT / "study_runner" / "web" / "scripts" / "admin" / "sessions-browser.js"
        ).read_text(encoding="utf-8")

        self.assertIn('data-session-folder="${escapeHtml(session.session_folder)}"', source)
        self.assertIn("params.set('session_folder', sessionFolder)", source)
        self.assertIn("session_folder=${encodeURIComponent(session.session_folder)}", source)
        self.assertNotIn("result_file=", source)


if __name__ == "__main__":
    unittest.main()
