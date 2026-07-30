"""Fetch DeepFace model assets required by offline packaged builds."""
from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "software"
    / "study_runner"
    / "integrations"
    / "local_emotion_worker"
    / "model_assets"
)
EMOTION_MODEL_NAME = "facial_expression_model_weights.h5"
EMOTION_MODEL_URL = (
    "https://github.com/serengil/deepface_models/releases/download/v1.0/"
    "facial_expression_model_weights.h5"
)
MIN_BYTES = 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch DeepFace model assets for offline Study Runner releases."
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output directory for vendored model assets.",
    )
    parser.add_argument(
        "--source-file",
        default="",
        help="Copy an already downloaded model file instead of downloading it.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing valid asset.")
    args = parser.parse_args()

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
    if bytes_downloaded < MIN_BYTES:
        temporary.unlink(missing_ok=True)
        raise SystemExit(f"Downloaded file is too small: {bytes_downloaded} bytes")
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
        return path.is_file() and path.stat().st_size >= MIN_BYTES
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
