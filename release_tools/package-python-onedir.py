from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser(description="Package the PyInstaller one-dir server build as a zip asset.")
    parser.add_argument("--source", required=True, help="Path to software/dist/study-runner-server")
    parser.add_argument("--output", required=True, help="Output zip file")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")

    output.parent.mkdir(parents=True, exist_ok=True)
    base = source.parent
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(source.rglob("*")):
            archive.write(path, path.relative_to(base).as_posix())

    print(f"Packaged {source} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
