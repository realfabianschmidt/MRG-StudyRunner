"""Explicitly provision the separately licensed DeepFace emotion model."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EMOTION_MODEL_NAME = "facial_expression_model_weights.h5"
EMOTION_MODEL_URL = (
    "https://github.com/serengil/deepface_models/releases/download/v1.0/"
    "facial_expression_model_weights.h5"
)
MIN_BYTES = 1_000_000
EXPECTED_SHA256 = "e8e8851d3fa05c001b1c27fd8841dfe08d7f82bb786a53ad8776725b7a1e824c"
TERMS_URLS = (
    "https://github.com/serengil/deepface_models",
    "https://www.robots.ox.ac.uk/~vgg/software/vgg_face/",
)


def default_output() -> Path:
    configured_data = os.environ.get("STUDY_RUNNER_DATA_DIR", "").strip()
    storage_root = (
        Path(configured_data).expanduser()
        if configured_data
        else REPO_ROOT / "software"
    )
    return (
        storage_root
        / "runtime"
        / "camera_emotion"
        / "worker"
        / "deepface_home"
        / ".deepface"
        / "weights"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Provision the optional DeepFace emotion weights after reviewing "
            "their inherited VGG-Face terms. The model is not part of a Study "
            "Runner source release."
        )
    )
    parser.add_argument(
        "--output",
        default=str(default_output()),
        help="Runtime DeepFace weights directory (defaults below STUDY_RUNNER_DATA_DIR).",
    )
    parser.add_argument(
        "--source-file",
        default="",
        help="Copy an already downloaded model file instead of downloading it.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing valid asset.")
    parser.add_argument(
        "--accept-vgg-face-non-commercial-research-terms",
        action="store_true",
        help=(
            "Confirm that you reviewed the upstream model terms and that this "
            "non-commercial research use is permitted."
        ),
    )
    args = parser.parse_args()

    if not args.accept_vgg_face_non_commercial_research_terms:
        joined = "\n  - ".join(TERMS_URLS)
        raise SystemExit(
            "The model is separately licensed and is not distributed with Study Runner.\n"
            "Review the current terms at:\n"
            f"  - {joined}\n"
            "If your use is permitted, rerun with "
            "--accept-vgg-face-non-commercial-research-terms."
        )

    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / EMOTION_MODEL_NAME
    if destination.exists() and not args.force and _valid_asset(destination):
        print(f"DeepFace emotion model already present: {destination}")
        return 0

    temporary = destination.with_name(f"{destination.name}.tmp")
    if args.source_file:
        source = Path(args.source_file).expanduser().resolve()
        if not _valid_asset(source):
            raise SystemExit(f"Source model asset is missing or too small: {source}")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
        print(f"Copied DeepFace emotion model to {destination}")
        return 0

    print(f"Downloading {EMOTION_MODEL_URL}")
    bytes_downloaded = _download(EMOTION_MODEL_URL, temporary)
    if bytes_downloaded < MIN_BYTES or not _valid_asset(temporary):
        temporary.unlink(missing_ok=True)
        raise SystemExit(
            f"Downloaded model failed size/SHA-256 verification: {bytes_downloaded} bytes"
        )
    temporary.replace(destination)
    print(f"DeepFace emotion model ready: {destination} ({bytes_downloaded} bytes)")
    return 0


def _download(url: str, destination: Path) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "MRG-StudyRunner/DeepFaceAssetFetch"})
    bytes_downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    bytes_downloaded += len(chunk)
                    print(f"Downloaded {bytes_downloaded} bytes", end="\r")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    print()
    return bytes_downloaded


def _valid_asset(path: Path) -> bool:
    try:
        return (
            path.is_file()
            and path.stat().st_size >= MIN_BYTES
            and _sha256(path) == EXPECTED_SHA256
        )
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
