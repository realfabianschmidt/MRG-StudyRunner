"""Build the offline dependency wheelhouse used by packaged releases."""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = REPO_ROOT / "software" / "requirements.txt"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "release_tools" / "wheelhouse"


def _default_platform_label() -> str:
    system = platform.system().lower() or sys.platform
    machine = platform.machine().lower() or "unknown"
    return f"{system}-{machine}-py{sys.version_info.major}{sys.version_info.minor}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an offline pip wheelhouse for Study Runner runtime dependencies."
    )
    parser.add_argument(
        "--requirements",
        default=str(DEFAULT_REQUIREMENTS),
        help="Requirements file to wheel. Defaults to software/requirements.txt.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output directory. Defaults to release_tools/wheelhouse/<platform>.",
    )
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Wheel only the packages listed directly in requirements.txt.",
    )
    args = parser.parse_args()

    requirements_file = Path(args.requirements).expanduser().resolve()
    if not requirements_file.exists():
        raise SystemExit(f"Requirements file not found: {requirements_file}")

    output_dir = Path(args.output).expanduser().resolve() if args.output else DEFAULT_OUTPUT_ROOT / _default_platform_label()
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--wheel-dir",
        str(output_dir),
        "-r",
        str(requirements_file),
    ]
    if args.no_deps:
        command.append("--no-deps")

    subprocess.run(command, cwd=REPO_ROOT, check=True)
    print(f"Offline wheelhouse ready: {output_dir}")
    print("Install with: python -m pip install --no-index --find-links <wheelhouse> -r software/requirements.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
