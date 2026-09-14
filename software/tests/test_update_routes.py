from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.apps.server import create_app
from study_runner.runtime_core.settings import update_service


class UpdateRoutesTests(unittest.TestCase):
    def test_source_update_status_needs_no_public_key(self) -> None:
        # Source mode verifies an update through git and the release tag
        # instead of a signing key, so it is "configured" without one --
        # unlike a packaged build (see the next test).
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=True):
                app = create_app()

            response = app.test_client().get("/api/admin/update/status")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["source_mode"])
        self.assertEqual(payload["state"], "idle")

    def test_packaged_update_status_reports_release_key_problem(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_APP_MODE": "packaged",
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=True):
                with patch.object(sys, "frozen", True, create=True):
                    app = create_app()
                    response = app.test_client().get("/api/admin/update/status")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["configured"])
        self.assertFalse(payload["source_mode"])
        self.assertIn("public key", payload["configuration_error"].lower())

    def test_source_update_check_reports_already_current(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=True):
                app = create_app()
                metadata = {"version": update_service.__version__, "tag": "app-vcurrent"}
                with patch.object(update_service, "fetch_source_release_metadata", return_value=metadata):
                    response = app.test_client().post("/api/admin/update/check")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "current")
        self.assertFalse(payload["update"]["available"])

    def test_update_check_returns_available_update(self) -> None:
        private_key, public_key = _make_keypair()
        platform_key = update_service.detect_platform_key()
        asset = _signed_asset(private_key, "9.9.9", platform_key)
        manifest = update_service.normalize_manifest(
            {
                "version": "9.9.9",
                "notes_url": "https://example.com/release",
                "minimum_updater_version": 1,
                "assets": {platform_key: asset},
            }
        )

        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_APP_MODE": "packaged",
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
                "STUDY_RUNNER_UPDATE_PUBLIC_KEY": public_key,
            }
            with patch.dict(os.environ, env, clear=True):
                with patch.object(sys, "frozen", True, create=True):
                    app = create_app()
                    with patch.object(update_service, "fetch_manifest", return_value=manifest):
                        response = app.test_client().post("/api/admin/update/check")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["state"], "available")
        self.assertTrue(payload["update"]["available"])
        self.assertEqual(payload["update"]["version"], "9.9.9")

    def test_source_update_check_reports_an_available_release(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            env = {
                "STUDY_RUNNER_DATA_DIR": data_dir,
                "STUDY_RUNNER_DISABLE_HARDWARE": "1",
            }
            with patch.dict(os.environ, env, clear=True):
                app = create_app()
                metadata = {"version": "99.0.0", "tag": "app-v99.0.0"}
                with patch.object(update_service, "fetch_source_release_metadata", return_value=metadata):
                    response = app.test_client().post("/api/admin/update/check")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["source_mode"])
        self.assertEqual(payload["state"], "available")
        self.assertEqual(payload["update"]["version"], "99.0.0")
        self.assertIn("app-v99.0.0", payload["update"]["notes_url"])


def _make_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, base64.b64encode(public_bytes).decode("ascii")


def _signed_asset(private_key: Ed25519PrivateKey, version: str, platform_key: str) -> dict:
    asset = {
        "url": "https://example.com/study-runner-server.zip",
        "sha256": "a" * 64,
        "size": 123,
        "file_name": "study-runner-server.zip",
    }
    signature = private_key.sign(update_service.canonical_asset_payload(version, platform_key, asset))
    asset["signature"] = base64.b64encode(signature).decode("ascii")
    return asset


if __name__ == "__main__":
    unittest.main()
