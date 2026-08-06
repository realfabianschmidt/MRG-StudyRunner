"""Build and verify the source-first Study Runner release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "study-runner/source-release/v1"
ARCHIVES = ("study-runner-source.zip", "study-runner-source.tar.gz")
THIRD_PARTY_NOTICE_FILES = (
    "THIRD_PARTY_NOTICES.md",
    "software/recording_worker/native/vendor/App-LabRecorder/LICENSE",
    "software/recording_worker/native/vendor/BOOST_LICENSE_1_0.txt",
    "software/study_runner/frontend/vendor/iconoir/LICENSE",
    "software/study_runner/frontend/vendor/geist/LICENSE",
)
REQUIRED_SOURCE_FILES = (
    "LICENSE",
    *THIRD_PARTY_NOTICE_FILES,
    "CHANGELOG.md",
    "README.md",
    "software/server.py",
    "software/requirements.txt",
    "software/constraints/py312-bootstrap.txt",
    "software/constraints/py312-common.txt",
    "software/constraints/py312-local-emotion.txt",
    "tools/install-windows.ps1",
    "tools/start-windows.ps1",
    "tools/install-macos.sh",
    "tools/start-macos.sh",
    "tools/setup_recording_worker.py",
    "software/recording_worker/native/CMakeLists.txt",
    "software/recording_worker/native/UPSTREAM_LOCK.json",
)
FORBIDDEN_ARCHIVE_PARTS = (
    "/.git/",
    "/.venv/",
    "/.build/",
    "/build/",
    "/dist/",
    "/__pycache__/",
    "/saved_results/",
    "/local_secrets.json",
    "/worker-build.json",
)
FORBIDDEN_SOURCE_SUFFIXES = (
    ".dll",
    ".dylib",
    ".so",
    ".exe",
    ".lib",
    ".a",
    ".o",
    ".obj",
    ".pyc",
    ".pfx",
    ".p12",
    ".pem",
    ".key",
    ".crt",
    ".cer",
    ".h5",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".toe",
)
# Fonts stay forbidden by default so a stray face can never ride along unnoticed.
# One may only ship from a folder that documents its terms: frontend/fonts/ holds
# the first-party Materiability faces covered by this repository's own LICENSE,
# and frontend/vendor/geist/ carries the upstream OFL text the contract checks.
LICENSED_FONT_DIRECTORIES = (
    "software/study_runner/frontend/fonts/",
    "software/study_runner/frontend/vendor/geist/",
)
LICENSED_FONT_SUFFIXES = (".ttf", ".woff2")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SUPPORTED_RECORDING_TARGETS = ("windows-x64", "macos-x64", "macos-arm64")
INSTALL_COMMANDS = {
    "windows_first_install": (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        "tools/install-windows.ps1 -InstallSystemDependencies"
    ),
    "windows_later_start": ".\\tools\\start-windows.ps1",
    "macos_first_install": (
        "bash tools/install-macos.sh --install-system-dependencies"
    ),
    "macos_later_start": "bash tools/start-macos.sh",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ReleaseError(RuntimeError):
    """A release artifact is inconsistent or unsafe to publish."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_members(path: Path) -> tuple[str, ...]:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return tuple(member.filename for member in archive.infolist())
    with tarfile.open(path, "r:gz") as archive:
        return tuple(member.name for member in archive.getmembers())


def validate_archive_member_types(path: Path) -> None:
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(unix_mode):
                    raise ReleaseError(
                        f"symbolic link is not allowed in {path.name}: {member.filename}"
                    )
        return
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise ReleaseError(f"link is not allowed in {path.name}: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ReleaseError(f"special file is not allowed in {path.name}: {member.name}")


def validate_archive(path: Path, *, version: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ReleaseError(f"release archive is missing or empty: {path}")
    validate_archive_member_types(path)
    members = archive_members(path)
    if not members:
        raise ReleaseError(f"release archive is empty: {path}")
    normalized_list: list[str] = []
    for raw_name in members:
        slash_name = raw_name.replace("\\", "/")
        parts = slash_name.split("/")
        if (
            slash_name.startswith("/")
            or re.match(r"^[A-Za-z]:", slash_name)
            or any(part in {"", ".", ".."} for part in parts[:-1])
            or (parts and parts[-1] in {".", ".."})
            or "\x00" in slash_name
        ):
            raise ReleaseError(f"unsafe path in {path.name}: {raw_name}")
        for part in parts:
            if re.search(r"[<>:\"|?*]", part) or part.rstrip(" .") != part:
                raise ReleaseError(f"Windows-incompatible path in {path.name}: {raw_name}")
            if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
                raise ReleaseError(f"Windows-reserved path in {path.name}: {raw_name}")
        normalized_list.append(f"/{slash_name.lstrip('/')}")
    normalized = tuple(normalized_list)
    if len(set(normalized)) != len(normalized):
        raise ReleaseError(f"duplicate path in release archive: {path}")
    if len({name.casefold() for name in normalized}) != len(normalized):
        raise ReleaseError(f"case-colliding path is not portable to Windows: {path}")
    top_levels = {name.strip("/").split("/", 1)[0] for name in normalized if name.strip("/")}
    if len(top_levels) != 1:
        raise ReleaseError(f"release archive must contain exactly one root directory: {path}")
    expected_root = f"MRG-StudyRunner-{version}" if version else next(iter(top_levels))
    if top_levels != {expected_root}:
        raise ReleaseError(
            f"release archive root is {sorted(top_levels)!r}, expected {expected_root!r}"
        )
    names = set(normalized)
    for relative in REQUIRED_SOURCE_FILES:
        expected = f"/{expected_root}/{relative}"
        if expected not in names:
            raise ReleaseError(f"required source file is absent from {path.name}: {relative}")
    for name in normalized:
        lowered = name.casefold()
        if any(part.casefold() in lowered for part in FORBIDDEN_ARCHIVE_PARTS):
            raise ReleaseError(f"generated, native, or private path leaked into {path.name}: {name}")
        if lowered.endswith(FORBIDDEN_SOURCE_SUFFIXES) and not is_licensed_font(lowered):
            raise ReleaseError(f"binary or secret-like file leaked into {path.name}: {name}")


def is_licensed_font(lowered_name: str) -> bool:
    """True for a font sitting directly in a folder that documents its terms.

    The archive member is prefixed with the release root, so match on the
    repository-relative tail rather than the whole path. A nested folder does not
    inherit the exemption: whoever adds one has to document its terms too.
    """
    if not lowered_name.endswith(LICENSED_FONT_SUFFIXES):
        return False
    for directory in LICENSED_FONT_DIRECTORIES:
        marker = f"/{directory.casefold()}"
        start = lowered_name.find(marker)
        if start >= 0 and "/" not in lowered_name[start + len(marker):]:
            return True
    return False


def git_archive(*, commit: str, version: str, output: Path, archive_format: str) -> None:
    prefix = f"MRG-StudyRunner-{version}/"
    command = (
        "git",
        "archive",
        f"--format={archive_format}",
        f"--prefix={prefix}",
        f"--output={output}",
        commit,
    )
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(f"git archive failed: {result.stderr.strip() or result.stdout.strip()}")


def git_text(commit: str, relative_path: str) -> str:
    result = subprocess.run(
        ("git", "show", f"{commit}:{relative_path}"),
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(
            f"cannot read {relative_path} from release commit: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def changelog_section(changelog: str, version: str) -> str:
    heading = re.compile(
        rf"^##[ \t]+{re.escape(version)}(?:[ \t]+-[ \t]+[^\n]+)?[ \t]*$",
        re.MULTILINE,
    )
    match = heading.search(changelog)
    if match is None:
        raise ReleaseError(f"CHANGELOG.md has no release section for {version}")
    next_heading = re.search(r"^##[ \t]+", changelog[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(changelog)
    body = changelog[match.end() : end].strip()
    if not body:
        raise ReleaseError(f"CHANGELOG.md release section for {version} is empty")
    return body


def validate_repository_license(license_text: str) -> None:
    required_phrases = (
        "All rights reserved.",
        "proprietary",
        "Third-party components remain subject to their respective licenses.",
        "App-LabRecorder/XDFWriter",
    )
    missing = [phrase for phrase in required_phrases if phrase not in license_text]
    if missing:
        raise ReleaseError(f"repository LICENSE is missing required notice: {missing[0]}")


def validate_third_party_notices(notice_text: str) -> None:
    required_phrases = (
        "App-LabRecorder / XDFWriter",
        "native/src/xdfwriter_patched.cpp",
        "remain derived works under",
        "xdfwriter/conversions.h",
        "Boost Software License 1.0",
        "software/recording_worker/native/vendor/BOOST_LICENSE_1_0.txt",
        "Iconoir",
        "software/study_runner/frontend/vendor/iconoir/LICENSE",
        "Geist",
        "software/study_runner/frontend/vendor/geist/LICENSE",
        "facial_expression_model_weights.h5",
        "do **not** contain",
    )
    missing = [phrase for phrase in required_phrases if phrase not in notice_text]
    if missing:
        raise ReleaseError(
            f"THIRD_PARTY_NOTICES.md is missing required provenance: {missing[0]}"
        )


def validate_third_party_license(relative_path: str, license_text: str) -> None:
    requirements = {
        "software/recording_worker/native/vendor/App-LabRecorder/LICENSE": (
            "MIT License",
            "Permission is hereby granted",
        ),
        "software/recording_worker/native/vendor/BOOST_LICENSE_1_0.txt": (
            "Boost Software License - Version 1.0",
            "Permission is hereby granted",
        ),
        "software/study_runner/frontend/vendor/iconoir/LICENSE": (
            "MIT License",
            "Permission is hereby granted",
        ),
        "software/study_runner/frontend/vendor/geist/LICENSE": (
            "SIL OPEN FONT LICENSE Version 1.1",
            "Permission is hereby granted",
        ),
    }
    expected = requirements.get(relative_path)
    if expected is None:
        raise ReleaseError(f"no release license contract exists for {relative_path}")
    missing = [phrase for phrase in expected if phrase not in license_text]
    if missing:
        raise ReleaseError(
            f"{relative_path} is missing required license text: {missing[0]}"
        )


def build_release(
    *, version: str, repo: str, tag: str, commit: str, output_dir: Path
) -> dict[str, object]:
    if not SEMVER.fullmatch(version):
        raise ReleaseError(f"version must be numeric SemVer without a prefix: {version!r}")
    if tag != f"app-v{version}":
        raise ReleaseError(f"release tag must be app-v{version}, got {tag!r}")
    if not COMMIT.fullmatch(commit):
        raise ReleaseError("release commit must be a full lowercase Git SHA")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
        raise ReleaseError(f"repository must use owner/name form: {repo!r}")

    validate_repository_license(git_text(commit, "LICENSE"))
    validate_third_party_notices(git_text(commit, "THIRD_PARTY_NOTICES.md"))
    for relative_path in THIRD_PARTY_NOTICE_FILES[1:]:
        validate_third_party_license(relative_path, git_text(commit, relative_path))
    changes = changelog_section(git_text(commit, "CHANGELOG.md"), version)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {name: output_dir / name for name in ARCHIVES}
    git_archive(commit=commit, version=version, output=outputs[ARCHIVES[0]], archive_format="zip")
    git_archive(commit=commit, version=version, output=outputs[ARCHIVES[1]], archive_format="tar.gz")
    for path in outputs.values():
        validate_archive(path, version=version)

    artifacts = {
        name: {"sha256": sha256_file(path), "size": path.stat().st_size}
        for name, path in outputs.items()
    }
    metadata: dict[str, object] = {
        "schema": SCHEMA,
        "release_kind": "source_server",
        "version": version,
        "tag": tag,
        "commit": commit,
        "repository": repo,
        "license": {
            "identifier": "LicenseRef-Proprietary",
            "name": "Proprietary - all rights reserved",
            "file": "LICENSE",
            "third_party_notices": list(THIRD_PARTY_NOTICE_FILES),
        },
        "artifacts": artifacts,
        "recording": {
            "canonical_format": "XDF",
            "native_core_bundled": False,
            "local_setup_command": "python tools/setup_recording_worker.py --require-canonical",
            "supported_targets": SUPPORTED_RECORDING_TARGETS,
            "linux_recording_supported": False,
        },
        "packaged_updater_compatible": False,
        "install": INSTALL_COMMANDS,
    }
    metadata_path = output_dir / "study-runner-source-release.json"
    metadata_path.write_text(f"{json.dumps(metadata, indent=2, sort_keys=True)}\n", encoding="utf-8")
    checksum_targets = {
        **{name: str(details["sha256"]) for name, details in artifacts.items()},
        metadata_path.name: sha256_file(metadata_path),
    }
    checksums = "".join(f"{digest}  {name}\n" for name, digest in sorted(checksum_targets.items()))
    (output_dir / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    (output_dir / "verify_release_assets.py").write_text(
        _verification_script(checksum_targets), encoding="utf-8"
    )
    (output_dir / "RELEASE_NOTES.md").write_text(
        _release_notes(version, changes), encoding="utf-8"
    )
    verify_output(output_dir)
    return metadata


def _verification_script(expected: dict[str, str]) -> str:
    return (
        "from hashlib import sha256\n"
        "from pathlib import Path\n"
        f"EXPECTED = {expected!r}\n"
        "for name, wanted in EXPECTED.items():\n"
        "    path = Path(__file__).resolve().parent / name\n"
        "    actual = sha256(path.read_bytes()).hexdigest()\n"
        "    if actual != wanted:\n"
        "        raise SystemExit(f'{name}: expected {wanted}, got {actual}')\n"
        "print('Source release checksums verified.')\n"
    )


def _release_notes(version: str, changes: str) -> str:
    return f"""# Study Runner {version}\n\nThis is the proprietary source-server release for Windows x64, macOS Intel, and macOS Apple Silicon. All rights are reserved; see `LICENSE`. Third-party provenance and upstream license texts are listed in `THIRD_PARTY_NOTICES.md`.\n\n## Install and start\n\nAfter downloading and extracting `study-runner-source.zip` on Windows:\n\n```powershell\npowershell -NoProfile -ExecutionPolicy Bypass -File tools/install-windows.ps1 -InstallSystemDependencies\n.\\tools\\start-windows.ps1\n```\n\nAfter downloading and extracting `study-runner-source.tar.gz` on macOS:\n\n```bash\nbash tools/install-macos.sh --install-system-dependencies\nbash tools/start-macos.sh\n```\n\nThe native XDF core is intentionally not prebuilt or bundled. First install builds and verifies it locally from the pinned, vendored LabRecorder/XDFWriter sources. Linux may run non-recording development checks, but recording is not supported. Verify manual downloads with `SHA256SUMS`.\n\nThis is not a packaged-updater release and does not require Apple signing, notarization, or updater signing secrets.\n\n## Changes\n\n{changes}\n"""


def verify_output(output_dir: Path) -> dict[str, object]:
    output_dir = output_dir.resolve()
    metadata_path = output_dir / "study-runner-source-release.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseError(f"release metadata is unreadable: {error}") from error
    if not isinstance(metadata, dict) or metadata.get("schema") != SCHEMA:
        raise ReleaseError("release metadata has an unsupported schema")
    version = str(metadata.get("version") or "")
    if metadata.get("release_kind") != "source_server" or not SEMVER.fullmatch(version):
        raise ReleaseError("release metadata does not identify a valid source-server version")
    if metadata.get("tag") != f"app-v{version}":
        raise ReleaseError("release metadata tag does not match its version")
    if not COMMIT.fullmatch(str(metadata.get("commit") or "")):
        raise ReleaseError("release metadata has no full commit SHA")
    repository = str(metadata.get("repository") or "")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise ReleaseError("release metadata has no valid owner/name repository")
    if metadata.get("packaged_updater_compatible") is not False:
        raise ReleaseError("source release must not claim packaged-updater compatibility")
    recording = metadata.get("recording")
    if not isinstance(recording, dict) or recording.get("native_core_bundled") is not False:
        raise ReleaseError("source release must declare that the native core is not bundled")
    if (
        recording.get("canonical_format") != "XDF"
        or recording.get("linux_recording_supported") is not False
        or recording.get("supported_targets") != list(SUPPORTED_RECORDING_TARGETS)
        or recording.get("local_setup_command") != (
            "python tools/setup_recording_worker.py --require-canonical"
        )
    ):
        raise ReleaseError("release metadata has an invalid recording support contract")
    if metadata.get("install") != INSTALL_COMMANDS:
        raise ReleaseError("release metadata has an invalid install/start command contract")
    license_info = metadata.get("license")
    if (
        not isinstance(license_info, dict)
        or license_info.get("identifier") != "LicenseRef-Proprietary"
        or license_info.get("name") != "Proprietary - all rights reserved"
        or license_info.get("file") != "LICENSE"
        or license_info.get("third_party_notices") != list(THIRD_PARTY_NOTICE_FILES)
    ):
        raise ReleaseError("source release must declare the proprietary repository license")
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(ARCHIVES):
        raise ReleaseError("release metadata does not describe exactly the source archives")
    for name in ARCHIVES:
        path = output_dir / name
        validate_archive(path, version=version)
        details = artifacts.get(name)
        if not isinstance(details, dict):
            raise ReleaseError(f"release metadata is missing details for {name}")
        if details.get("sha256") != sha256_file(path) or details.get("size") != path.stat().st_size:
            raise ReleaseError(f"release metadata does not match {name}")
    checksum_targets = {
        **{name: str(artifacts[name]["sha256"]) for name in ARCHIVES},
        metadata_path.name: sha256_file(metadata_path),
    }
    expected_checksums = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(checksum_targets.items())
    )
    if (output_dir / "SHA256SUMS").read_text(encoding="utf-8") != expected_checksums:
        raise ReleaseError("SHA256SUMS does not match release metadata")
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    parser.add_argument("--repo")
    parser.add_argument("--tag")
    parser.add_argument("--commit")
    parser.add_argument("--output-dir")
    parser.add_argument("--verify-output")
    args = parser.parse_args(argv)
    try:
        if args.verify_output:
            verify_output(Path(args.verify_output))
            print(f"Verified source release output: {Path(args.verify_output).resolve()}")
            return 0
        required = {
            "--version": args.version,
            "--repo": args.repo,
            "--tag": args.tag,
            "--commit": args.commit,
            "--output-dir": args.output_dir,
        }
        missing = [flag for flag, value in required.items() if not value]
        if missing:
            raise ReleaseError(f"missing required build arguments: {', '.join(missing)}")
        build_release(
            version=args.version,
            repo=args.repo,
            tag=args.tag,
            commit=args.commit,
            output_dir=Path(args.output_dir),
        )
    except ReleaseError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "output_dir": str(Path(args.output_dir).resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
