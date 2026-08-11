"""The dev-server access log must not bury the startup banner in noise.

Werkzeug's default request handler logs every request at INFO level, success
and failure alike. The admin dashboard polls several status endpoints every
1-2 seconds, so that quickly drowns out the one-time startup banner and the
app's own rare, meaningful print lines under a constant stream of successful
GETs. QuietWSGIRequestHandler keeps Werkzeug's own formatting for genuine
errors and silences everything else.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# study_runner.app_server calls create_app() at import time, so the first
# import anywhere in the test session must happen against an isolated,
# disposable data directory -- never the real one.
if "study_runner.app_server" not in sys.modules:
    with tempfile.TemporaryDirectory() as _bootstrap_dir:
        _env = {
            "STUDY_RUNNER_DATA_DIR": _bootstrap_dir,
            "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            "STUDY_RUNNER_DISABLE_BACKGROUND": "1",
        }
        with patch.dict(os.environ, _env, clear=False):
            import study_runner.app_server as app_server
else:
    import study_runner.app_server as app_server


class QuietWSGIRequestHandlerTests(unittest.TestCase):
    def _handler(self):
        # Bypass WSGIRequestHandler.__init__ (it wants a real socket); only
        # log_request's own logic is under test.
        return app_server.QuietWSGIRequestHandler.__new__(app_server.QuietWSGIRequestHandler)

    def test_successful_requests_are_not_logged(self) -> None:
        handler = self._handler()
        with patch.object(app_server.WSGIRequestHandler, "log_request") as base_log:
            handler.log_request(200, "-")
            handler.log_request("304", "-")

        base_log.assert_not_called()

    def test_client_and_server_errors_are_logged(self) -> None:
        handler = self._handler()
        with patch.object(app_server.WSGIRequestHandler, "log_request") as base_log:
            handler.log_request(404, "-")
            handler.log_request("500", "-")

        self.assertEqual(base_log.call_count, 2)

    def test_an_unparsable_code_is_treated_as_success(self) -> None:
        handler = self._handler()
        with patch.object(app_server.WSGIRequestHandler, "log_request") as base_log:
            handler.log_request("-", "-")

        base_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
