from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the trusted Python updater public key for release builds.")
    parser.add_argument(
        "--output",
        default="software/study_runner/update_keys.py",
        help="Output Python module that defines TRUSTED_UPDATE_PUBLIC_KEYS.",
    )
    parser.add_argument("--key", default="", help="Base64 Ed25519 public key. Defaults to env.")
    args = parser.parse_args()

    raw_key = (args.key or os.getenv("PYTHON_UPDATER_PUBLIC_KEY") or os.getenv("STUDY_RUNNER_UPDATE_PUBLIC_KEY") or "").strip()
    if not raw_key:
        raise SystemExit("PYTHON_UPDATER_PUBLIC_KEY is required for release builds.")

    _validate_public_key(raw_key)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        '"""Trusted public keys for Python-only update verification."""\n\n'
        f"TRUSTED_UPDATE_PUBLIC_KEYS: list[str] = {json.dumps([raw_key], indent=2)}\n",
        encoding="utf-8",
    )
    print(f"Wrote Python updater public key to {output}")
    return 0


def _validate_public_key(raw_key: str) -> None:
    if "BEGIN PUBLIC KEY" in raw_key:
        return
    padded = raw_key + "=" * (-len(raw_key) % 4)
    try:
        decoded = base64.b64decode(padded.encode("ascii"), validate=False)
    except ValueError as error:
        raise SystemExit("PYTHON_UPDATER_PUBLIC_KEY must be base64 or PEM.") from error
    if len(decoded) != 32:
        raise SystemExit("PYTHON_UPDATER_PUBLIC_KEY must decode to a 32-byte Ed25519 public key.")


if __name__ == "__main__":
    raise SystemExit(main())
