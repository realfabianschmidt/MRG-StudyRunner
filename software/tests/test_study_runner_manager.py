from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import study_runner_manager as manager


class StudyRunnerManagerTests(unittest.TestCase):
    def test_platform_asset_names_for_windows_and_macos_arm(self) -> None:
        with patch("tools.study_runner_manager.platform.system", return_value="Windows"):
            with patch("tools.study_runner_manager.platform.machine", return_value="AMD64"):
                self.assertEqual(manager.detect_platform_key(), "windows-x86_64")
                self.assertEqual(manager.server_asset_name(), "study-runner-server-windows-x86_64.zip")
                self.assertEqual(manager.manager_asset_name(), "study-runner-manager-windows-x86_64.zip")

        with patch("tools.study_runner_manager.platform.system", return_value="Darwin"):
            with patch("tools.study_runner_manager.platform.machine", return_value="arm64"):
                self.assertEqual(manager.detect_platform_key(), "macos-arm64")
                self.assertEqual(manager.manager_asset_name(), "study-runner-manager-macos-arm64.zip")

    def test_manifest_signature_verifies_with_trusted_public_key(self) -> None:
        private_key, public_key = _make_keypair()
        platform_key = "windows-x86_64"
        asset = _signed_asset(private_key, "9.9.9", platform_key, "b" * 64)

        with patch("study_runner.updates.trusted_keys.TRUSTED_UPDATE_PUBLIC_KEYS", [public_key]):
            manager.verify_asset_signature("9.9.9", platform_key, asset)

    def test_safe_zip_extraction_rejects_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            zip_path = root / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("../evil.txt", "no")

            with self.assertRaises(RuntimeError):
                manager.extract_zip_safe(zip_path, root / "out")

            self.assertFalse((root.parent / "evil.txt").exists())

    def test_install_release_uses_versioned_folder_and_keeps_data_dir(self) -> None:
        private_key, public_key = _make_keypair()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "app"
            data_dir = root / "data"
            source_zip = _server_zip(root / "server.zip")
            sha256 = hashlib.sha256(source_zip.read_bytes()).hexdigest()
            platform_key = manager.detect_platform_key()
            asset = _signed_asset(private_key, "9.9.9", platform_key, sha256)
            asset["file_name"] = source_zip.name
            manifest = {"version": "9.9.9", "minimum_updater_version": 1, "assets": {platform_key: asset}}

            def fake_download(_url: str, destination: Path) -> str:
                destination.write_bytes(source_zip.read_bytes())
                return sha256

            with patch("study_runner.updates.trusted_keys.TRUSTED_UPDATE_PUBLIC_KEYS", [public_key]):
                with patch("tools.study_runner_manager.fetch_json", return_value=manifest):
                    with patch("tools.study_runner_manager.download_file", side_effect=fake_download):
                        result = manager.install_or_update_release(install_root, data_dir, lambda _msg: None)

            self.assertEqual(result.version, "9.9.9")
            self.assertEqual(result.install_dir, install_root / "versions" / "9.9.9")
            self.assertTrue(result.executable.exists())
            self.assertTrue(data_dir.exists())
            state = json.loads((install_root / "install-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["version"], "9.9.9")
            self.assertEqual(Path(state["data_dir"]), data_dir)

    def test_launcher_sets_data_dir_without_modifying_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "app"
            data_dir = root / "data folder"
            desktop = root / "Desktop"
            executable = install_root / "versions" / "1.0.0" / "study-runner-server" / "study-runner-server.exe"
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            data_dir.mkdir()
            before = sorted(data_dir.iterdir())

            with patch("tools.study_runner_manager.desktop_dir", return_value=desktop):
                with patch("tools.study_runner_manager.platform.system", return_value="Windows"):
                    desktop.mkdir()
                    launcher = manager.create_launcher(install_root, data_dir, lambda _msg: None)

            content = launcher.read_text(encoding="utf-8")
            self.assertIn(f'set "STUDY_RUNNER_DATA_DIR={data_dir}"', content)
            self.assertEqual(before, sorted(data_dir.iterdir()))

    def test_diagnostics_do_not_read_or_export_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            secrets_file = data_dir / "settings" / "local_secrets.json"
            secrets_file.parent.mkdir(parents=True)
            secrets_file.write_text('{"notion":{"api_key":"secret_should_not_appear"}}', encoding="utf-8")

            text = manager.build_diagnostics_text(root / "app", data_dir)

        self.assertIn("Study Runner Install & Repair Wizard Diagnostics", text)
        self.assertNotIn("secret_should_not_appear", text)


def _make_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, base64.b64encode(public_bytes).decode("ascii")


def _signed_asset(private_key: Ed25519PrivateKey, version: str, platform_key: str, sha256: str) -> dict:
    asset = {
        "url": "https://example.com/study-runner-server.zip",
        "sha256": sha256,
        "size": 123,
        "file_name": "study-runner-server.zip",
    }
    signature = private_key.sign(manager.canonical_asset_payload(version, platform_key, asset))
    asset["signature"] = base64.b64encode(signature).decode("ascii")
    return asset


def _server_zip(zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("study-runner-server/study-runner-server.exe", "")
    return zip_path


if __name__ == "__main__":
    unittest.main()
