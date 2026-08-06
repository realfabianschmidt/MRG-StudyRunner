"""Build and sign the Python release update manifest."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# The signature must cover exactly the bytes installed clients verify,
# so the payload builder is shared with the app (study_runner.updates.signatures).
SOFTWARE_ROOT = Path(__file__).resolve().parents[1] / "software"
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))

from study_runner.updates.signatures import UPDATER_SCHEMA_VERSION, canonical_asset_payload  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the signed Python update manifest for GitHub Releases.")
    parser.add_argument("--version", required=True, help="SemVer version without app-v prefix")
    parser.add_argument("--repo", required=True, help="GitHub repository, e.g. owner/name")
    parser.add_argument("--tag", required=True, help="Release tag, e.g. app-v0.3.0")
    parser.add_argument("--asset-dir", required=True, help="Directory containing study-runner-server-*.zip assets")
    parser.add_argument("--output", required=True, help="Output manifest JSON path")
    args = parser.parse_args()

    private_key = _load_private_key()
    asset_dir = Path(args.asset_dir).resolve()
    assets: dict[str, dict[str, object]] = {}

    for asset_path in sorted(asset_dir.rglob("study-runner-server-*.zip")):
        platform_key = _platform_key_from_name(asset_path.name)
        file_hash = _sha256(asset_path)
        asset = {
            "url": f"https://github.com/{args.repo}/releases/download/{args.tag}/{quote(asset_path.name)}",
            "sha256": file_hash,
            "size": asset_path.stat().st_size,
            "file_name": asset_path.name,
        }
        signature = private_key.sign(canonical_asset_payload(args.version, platform_key, asset))
        asset["signature"] = base64.b64encode(signature).decode("ascii")
        assets[platform_key] = asset

    if not assets:
        raise SystemExit(f"No Python update zip assets found in {asset_dir}")

    manifest = {
        "version": args.version,
        "notes_url": f"https://github.com/{args.repo}/releases/tag/{args.tag}",
        "minimum_updater_version": UPDATER_SCHEMA_VERSION,
        "assets": assets,
    }

    output = Path(args.output).resolve()
    output.write_text(f"{json.dumps(manifest, indent=2, sort_keys=True)}\n", encoding="utf-8")
    print(f"Wrote signed Python update manifest to {output}")
    return 0


def _load_private_key() -> Ed25519PrivateKey:
    raw_key = os.getenv("PYTHON_UPDATER_SIGNING_PRIVATE_KEY", "").strip()
    if not raw_key:
        raise SystemExit("PYTHON_UPDATER_SIGNING_PRIVATE_KEY is required.")

    if "BEGIN" in raw_key:
        key = serialization.load_pem_private_key(raw_key.encode("utf-8"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SystemExit("PYTHON_UPDATER_SIGNING_PRIVATE_KEY must be an Ed25519 private key.")
        return key

    padded = raw_key + "=" * (-len(raw_key) % 4)
    key_bytes = base64.b64decode(padded.encode("ascii"), validate=False)
    if len(key_bytes) != 32:
        raise SystemExit("PYTHON_UPDATER_SIGNING_PRIVATE_KEY must decode to a 32-byte Ed25519 private key.")
    return Ed25519PrivateKey.from_private_bytes(key_bytes)


def _platform_key_from_name(file_name: str) -> str:
    prefix = "study-runner-server-"
    suffix = ".zip"
    if not file_name.startswith(prefix) or not file_name.endswith(suffix):
        raise SystemExit(f"Unexpected update asset name: {file_name}")
    return file_name[len(prefix) : -len(suffix)]


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
