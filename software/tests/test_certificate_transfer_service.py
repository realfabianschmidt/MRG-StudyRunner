"""Root-CA transfer must be validated, bounded, and rollback-safe."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask


SOFTWARE_DIR = Path(__file__).resolve().parents[1]
if str(SOFTWARE_DIR) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_DIR))

from study_runner.backend.routes.certificate import bp
from study_runner.backend.services.delivery.certificate_transfer_service import (
    CertificateTransferError,
    apply_import,
    build_export,
    inspect_import,
)
from study_runner.backend.services.settings.ssl_service import ensure_local_ssl_certificate


class CertificateTransferServiceTests(unittest.TestCase):
    def _create_ca(self, settings_dir: Path) -> dict:
        with mock.patch(
            "study_runner.backend.services.settings.ssl_service._local_certificate_names",
            return_value=({"localhost"}, {"127.0.0.1"}),
        ):
            ensure_local_ssl_certificate(settings_dir)
        return build_export(settings_dir)

    def test_export_can_be_validated_without_exposing_key_in_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = self._create_ca(Path(temp_dir))
            description = inspect_import(payload)

        self.assertIn("root_ca_private_key", payload)
        self.assertNotIn("private_key_pem", {
            "fingerprint_sha256": description["fingerprint_sha256"],
            "expires_at": description["expires_at"],
        })
        self.assertIn(":", description["fingerprint_sha256"])

    def test_import_rejects_mismatched_certificate_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            payload = self._create_ca(Path(first))
            other = self._create_ca(Path(second))
            payload["root_ca_private_key"] = other["root_ca_private_key"]

            with self.assertRaisesRegex(CertificateTransferError, "do not belong together"):
                inspect_import(payload)

    def test_successful_import_retires_old_leaf_and_reissues_under_imported_ca(self) -> None:
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            source_payload = self._create_ca(Path(source))
            old_payload = self._create_ca(Path(target))

            result = apply_import(Path(target), source_payload)
            self.assertTrue(result["ok"])
            self.assertTrue(result["restart_required"])
            self.assertEqual(result["replaced_fingerprint_sha256"], old_payload["fingerprint_sha256"])
            self.assertTrue(result["reissued_server_files"])

            ssl_dir = Path(target) / "ssl"
            self.assertFalse((ssl_dir / "study-runner-local-server.crt").exists())
            with mock.patch(
                "study_runner.backend.services.settings.ssl_service._local_certificate_names",
                return_value=({"localhost"}, {"127.0.0.1"}),
            ):
                ensure_local_ssl_certificate(Path(target))

            self.assertEqual(build_export(Path(target))["fingerprint_sha256"], source_payload["fingerprint_sha256"])
            self.assertTrue((ssl_dir / "study-runner-local-server.crt").exists())

    def test_failed_second_atomic_write_restores_original_ca_pair(self) -> None:
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            imported = self._create_ca(Path(source))
            original = self._create_ca(Path(target))
            ssl_dir = Path(target) / "ssl"
            original_cert = (ssl_dir / "study-runner-local-root-ca.crt").read_bytes()
            original_key = (ssl_dir / "study-runner-local-root-ca.key").read_bytes()

            from study_runner.backend.services.delivery import certificate_transfer_service as service

            real_write = service.atomic_write_bytes
            calls = 0

            def fail_second_write(path: Path, payload: bytes) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated disk failure")
                real_write(path, payload)

            with mock.patch.object(service, "atomic_write_bytes", side_effect=fail_second_write):
                with self.assertRaisesRegex(CertificateTransferError, "Could not install"):
                    apply_import(Path(target), imported)

            self.assertEqual((ssl_dir / "study-runner-local-root-ca.crt").read_bytes(), original_cert)
            self.assertEqual((ssl_dir / "study-runner-local-root-ca.key").read_bytes(), original_key)
            self.assertEqual(build_export(Path(target))["fingerprint_sha256"], original["fingerprint_sha256"])


class CertificateTransferRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_dir = Path(self.temp_dir.name)
        with mock.patch(
            "study_runner.backend.services.settings.ssl_service._local_certificate_names",
            return_value=({"localhost"}, {"127.0.0.1"}),
        ):
            ensure_local_ssl_certificate(self.settings_dir)
        app = Flask(__name__)
        app.config.update(TESTING=True, SETTINGS_DIR=str(self.settings_dir))
        app.register_blueprint(bp)
        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_export_is_downloaded_without_browser_cache(self) -> None:
        response = self.client.get("/api/admin/certificate/export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertEqual(json.loads(response.data)["study_runner_root_ca_export"], 1)

    def test_import_rejects_oversized_upload_before_parsing(self) -> None:
        response = self.client.post(
            "/api/admin/certificate/import",
            data=b"x" * (1024 * 1024 + 1),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("1 MB", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
