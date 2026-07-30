"""Characterization tests that lock the HTTP surface and updater wire format.

These are the safety net for structural refactors (blueprint split,
updater-crypto dedup): if a route is dropped/renamed or the signed
payload bytes change, an installed older client would break silently.
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

from study_runner.backend import create_app


EXPECTED_ROUTES = {
    ("GET", "/"),
    ("GET", "/admin"),
    ("POST", "/api/admin/brainbit/restart"),
    ("POST", "/api/admin/brainbit/select-device"),
    ("POST", "/api/admin/brainbit/start"),
    ("POST", "/api/admin/brainbit/stop"),
    ("GET", "/api/admin/camera/live/status"),
    ("POST", "/api/admin/camera/start"),
    ("POST", "/api/admin/camera/stop"),
    ("GET", "/api/admin/certificate/export"),
    ("POST", "/api/admin/certificate/import"),
    ("GET", "/api/admin/certificate/status"),
    ("POST", "/api/admin/emotion-worker/install-dependencies"),
    ("POST", "/api/admin/emotion-worker/repair-runtime"),
    ("POST", "/api/admin/integrations/<integration_key>/<action>"),
    ("POST", "/api/admin/integrations/<integration_key>/enabled"),
    ("POST", "/api/admin/radar/restart"),
    ("POST", "/api/admin/radar/start"),
    ("POST", "/api/admin/radar/stop"),
    ("POST", "/api/admin/restart"),
    ("POST", "/api/admin/session-overrides/reset"),
    ("GET", "/api/admin/sessions"),
    ("GET", "/api/admin/sessions/<study_id>/<participant_id>"),
    ("GET", "/api/admin/sessions/<study_id>/<participant_id>/signals"),
    ("GET", "/api/admin/status"),
    ("GET", "/api/admin/studies"),
    ("DELETE", "/api/admin/studies/<study_id>"),
    ("GET", "/api/admin/studies/<study_id>"),
    ("POST", "/api/admin/studies/active"),
    ("POST", "/api/admin/system/create-shortcut"),
    ("POST", "/api/admin/update/check"),
    ("POST", "/api/admin/update/download"),
    ("POST", "/api/admin/update/install"),
    ("GET", "/api/admin/update/status"),
    ("POST", "/api/camera/frame"),
    ("GET", "/api/config"),
    ("POST", "/api/config"),
    ("GET", "/api/hardware-config"),
    ("POST", "/api/hardware-config"),
    ("GET", "/api/health"),
    ("POST", "/api/marker"),
    ("POST", "/api/notion/flush-queue"),
    ("GET", "/api/notion/status"),
    ("POST", "/api/notion/test"),
    ("POST", "/api/nextcloud/test"),
    ("POST", "/api/results"),
    ("POST", "/api/results/partial"),
    ("GET", "/api/runtime-info"),
    ("POST", "/api/start"),
    ("POST", "/api/stop"),
    ("POST", "/api/study-client/heartbeat"),
    ("POST", "/api/study/camera-monitor/start"),
    ("GET", "/api/study/runtime"),
    ("POST", "/api/study/session/client-event"),
    ("POST", "/api/study/session/resume"),
    ("POST", "/api/study/session/start"),
    ("POST", "/api/study/session/stop"),
    ("POST", "/api/sync-clock"),
    ("GET", "/audit"),
    ("GET", "/static/<path:filename>"),
}


def _make_app(data_dir: str):
    env = {
        "STUDY_RUNNER_DATA_DIR": data_dir,
        "STUDY_RUNNER_DISABLE_HARDWARE": "1",
    }
    with patch.dict(os.environ, env, clear=False):
        return create_app()


class RouteInventoryTests(unittest.TestCase):
    def test_http_surface_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = _make_app(data_dir)
            actual = {
                (method, rule.rule)
                for rule in app.url_map.iter_rules()
                for method in rule.methods
                if method not in {"HEAD", "OPTIONS"}
            }

        missing = EXPECTED_ROUTES - actual
        added = actual - EXPECTED_ROUTES
        self.assertFalse(missing, f"routes disappeared: {sorted(missing)}")
        self.assertFalse(added, f"new routes must be added to EXPECTED_ROUTES: {sorted(added)}")

    def test_pages_and_core_endpoints_respond(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            app = _make_app(data_dir)
            client = app.test_client()

            for path in ("/", "/admin", "/audit"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertIn(b"<", response.data)

            config = client.get("/api/config")
            self.assertEqual(config.status_code, 200)
            self.assertIn("study_id", config.get_json())

            studies = client.get("/api/admin/studies")
            self.assertEqual(studies.status_code, 200)

            sync = client.post("/api/sync-clock", json={"client_send_ms": 12.5})
            payload = sync.get_json()
            self.assertEqual(sync.status_code, 200)
            self.assertEqual(payload["client_send_ms"], 12.5)
            self.assertLessEqual(payload["server_receive_ms"], payload["server_send_ms"])

            runtime = client.get("/api/study/runtime")
            self.assertEqual(runtime.status_code, 200)


class UpdaterWireFormatTests(unittest.TestCase):
    def test_canonical_asset_payload_bytes_are_frozen(self) -> None:
        """Installed 0.3.x clients verify signatures over exactly these bytes.

        If this test fails after a refactor, older installations can no
        longer verify new release manifests. Do not change the expected
        bytes; change the code back.
        """
        from study_runner.backend.services.update_service import canonical_asset_payload

        payload = canonical_asset_payload(
            "9.9.9",
            "windows-x86_64",
            {
                "url": "https://example.com/asset.zip",
                "sha256": "AABBCC",
                "size": 12345,
                "extra_field_is_ignored": True,
            },
        )

        self.assertEqual(
            payload,
            b'{"platform":"windows-x86_64","schema":1,"sha256":"aabbcc",'
            b'"size":12345,"url":"https://example.com/asset.zip","version":"9.9.9"}',
        )


if __name__ == "__main__":
    unittest.main()
