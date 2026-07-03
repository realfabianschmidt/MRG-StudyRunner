from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPO_ROOT / "software"
DEFAULT_SPEC = REPO_ROOT / "release_tools" / "pyinstaller" / "study_runner_server_onedir.spec"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Python-only Study Runner one-dir app.")
    parser.add_argument(
        "--spec",
        default=str(DEFAULT_SPEC),
        help="PyInstaller spec file. Defaults to release_tools/pyinstaller/study_runner_server_onedir.spec.",
    )
    args = parser.parse_args()

    spec_file = Path(args.spec).expanduser().resolve()
    if not spec_file.exists():
        raise SystemExit(f"PyInstaller spec not found: {spec_file}")

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec_file)],
        cwd=SOFTWARE_ROOT,
        check=True,
    )
    print("PyInstaller one-dir build is ready in software/dist/study-runner-server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
