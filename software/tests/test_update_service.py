from __future__ import annotations

import base64
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.settings import update_service


class FakeDownloadResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"content-length": str(len(content))}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index : index + chunk_size]

    def close(self) -> None:
        return None


class UpdateServiceTests(unittest.TestCase):
    def test_compare_versions(self) -> None:
        self.assertEqual(update_service.compare_versions("0.3.0", "0.2.9"), 1)
        self.assertEqual(update_service.compare_versions("0.2.2", "0.2.2"), 0)
        self.assertEqual(update_service.compare_versions("0.2.1", "0.2.2"), -1)

    def test_asset_signature_verification_accepts_generated_key(self) -> None:
        private_key, public_key = _make_keypair()
        asset = _signed_asset(private_key, "0.9.0", "windows-x86_64")

        with patch.dict(os.environ, {"STUDY_RUNNER_UPDATE_PUBLIC_KEY": public_key}, clear=False):
            update_service.verify_asset_signature("0.9.0", "windows-x86_64", asset)

    def test_asset_signature_verification_rejects_tampering(self) -> None:
        private_key, public_key = _make_keypair()
        asset = _signed_asset(private_key, "0.9.0", "windows-x86_64")
        asset["sha256"] = "b" * 64

        with patch.dict(os.environ, {"STUDY_RUNNER_UPDATE_PUBLIC_KEY": public_key}, clear=False):
            with self.assertRaises(update_service.UpdateError):
                update_service.verify_asset_signature("0.9.0", "windows-x86_64", asset)

    def test_manifest_normalization_and_platform_selection(self) -> None:
        private_key, _public_key = _make_keypair()
        asset = _signed_asset(private_key, "0.9.0", "linux-x86_64")
        manifest = update_service.normalize_manifest(
            {
                "version": "0.9.0",
                "minimum_updater_version": 1,
                "assets": {"linux-x86_64": asset},
            }
        )

        selected = update_service.select_platform_asset(manifest, "linux-x86_64")
        self.assertEqual(selected["sha256"], asset["sha256"])

    def test_download_and_stage_update_zip(self) -> None:
        private_key, public_key = _make_keypair()
        zip_bytes = _make_update_zip()
        sha256 = update_service.hashlib.sha256(zip_bytes).hexdigest()
        platform_key = update_service.detect_platform_key()
        asset = _signed_asset(
            private_key,
            "9.9.9",
            platform_key,
            sha256=sha256,
            size=len(zip_bytes),
            url="https://example.com/study-runner-server-test.zip",
        )
        manifest = {"version": "9.9.9", "minimum_updater_version": 1, "assets": {platform_key: asset}}

        with tempfile.TemporaryDirectory() as temp_dir:
            app_config = {"STORAGE_ROOT": temp_dir, "APP_MODE": "python", "BASE_DIR": PROJECT_ROOT}
            with patch.dict(os.environ, {"STUDY_RUNNER_UPDATE_PUBLIC_KEY": public_key}, clear=False):
                with patch.object(update_service, "fetch_manifest", return_value=update_service.normalize_manifest(manifest)):
                    check_status = update_service.check_for_update(app_config)
                self.assertTrue(check_status["update"]["available"])

                with patch.object(update_service.requests, "get", return_value=FakeDownloadResponse(zip_bytes)):
                    staged_status = update_service.download_and_stage_update(app_config)

                self.assertEqual(staged_status["state"], "staged")
                self.assertEqual(staged_status["staged"]["version"], "9.9.9")
                self.assertTrue(Path(staged_status["staged"]["path"]).exists())


def _make_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, base64.b64encode(public_bytes).decode("ascii")


def _signed_asset(
    private_key: Ed25519PrivateKey,
    version: str,
    platform_key: str,
    *,
    sha256: str = "a" * 64,
    size: int = 123,
    url: str = "https://example.com/study-runner-server.zip",
) -> dict:
    asset = {
        "url": url,
        "sha256": sha256,
        "size": size,
        "file_name": Path(url).name,
    }
    signature = private_key.sign(update_service.canonical_asset_payload(version, platform_key, asset))
    asset["signature"] = base64.b64encode(signature).decode("ascii")
    return asset


def _make_update_zip() -> bytes:
    executable_name = "study-runner-server.exe" if os.name == "nt" else "study-runner-server"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"study-runner-server/{executable_name}", "placeholder executable")
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
