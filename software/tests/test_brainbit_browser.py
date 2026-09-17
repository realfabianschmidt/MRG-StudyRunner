"""Opt-in real Chromium check; never starts the app, hardware, or recording."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('STUDY_RUNNER_BROWSER_TEST'), 'browser executable not provided')
class BrainBitBrowserTests(unittest.TestCase):
    def test_dropdown_survives_real_polling_and_sends_one_action(self):
        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                if path.startswith('/api/plugins/brainbit/assets/'):
                    path = '/software/study_runner/plugins/sensors/brainbit/' + path.split('/assets/', 1)[1]
                return super().translate_path(path)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for width in (1280, 768):
                with self.subTest(width=width), tempfile.TemporaryDirectory() as profile:
                    screenshot = ROOT / '.tmp' / f'brainbit-dashboard-{width}.png'
                    result = subprocess.run([
                        os.environ['STUDY_RUNNER_BROWSER_TEST'], '--headless=new', '--disable-gpu',
                        '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                        f'--user-data-dir={profile}', f'--window-size={width},1200',
                        '--virtual-time-budget=4000', '--dump-dom', f'--screenshot={screenshot}',
                        f'http://127.0.0.1:{server.server_port}/software/tests/js/brainbit-dashboard.browser.html',
                    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.assertIn('data-test-status="passed"', result.stdout, result.stdout[-5000:] + result.stderr[-1000:])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
