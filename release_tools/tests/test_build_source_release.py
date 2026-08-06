from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re
import stat
import tarfile
import tempfile
import unittest
import zipfile

from release_tools import build_source_release as release


VERSION = "1.2.3"
ROOT = f"MRG-StudyRunner-{VERSION}"


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    fixture_root = release.REPOSITORY_ROOT / ".tmp" / "source-release-tests"
    fixture_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=fixture_root)


def members(*extra: str) -> tuple[str, ...]:
    return tuple(f"{ROOT}/{name}" for name in (*release.REQUIRED_SOURCE_FILES, *extra))


def write_zip(path: Path, names: tuple[str, ...]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"source fixture\n")


def write_tar(path: Path, names: tuple[str, ...]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name in names:
            content = b"source fixture\n"
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))


class SourceReleaseTests(unittest.TestCase):
    def test_source_archives_have_one_safe_root_and_required_files(self) -> None:
        with temporary_directory() as temporary:
            root = Path(temporary)
            zip_path = root / release.ARCHIVES[0]
            tar_path = root / release.ARCHIVES[1]
            write_zip(zip_path, members())
            write_tar(tar_path, members())

            release.validate_archive(zip_path, version=VERSION)
            release.validate_archive(tar_path, version=VERSION)

    def test_archive_rejects_native_build_output(self) -> None:
        with temporary_directory() as temporary:
            path = Path(temporary) / release.ARCHIVES[0]
            write_zip(path, members("software/.build/xdf_core/windows-x64/xdf_core.dll"))

            with self.assertRaisesRegex(release.ReleaseError, "leaked"):
                release.validate_archive(path, version=VERSION)

    def test_archive_rejects_models_fonts_and_opaque_project_files(self) -> None:
        for suffix in (".h5", ".ttf", ".otf", ".woff", ".woff2", ".toe"):
            with self.subTest(suffix=suffix), temporary_directory() as temporary:
                path = Path(temporary) / release.ARCHIVES[0]
                write_zip(path, members(f"software/private/unlicensed{suffix}"))

                with self.assertRaisesRegex(release.ReleaseError, "leaked"):
                    release.validate_archive(path, version=VERSION)

    def test_archive_still_rejects_undocumented_fonts_next_to_the_licensed_ones(self) -> None:
        """The exemption is the vendor folder, not the file extension."""
        for name in (
            "software/study_runner/web/fonts/Materiability-Regular.ttf",
            "software/study_runner/web/vendor/geist/Geist-Regular.ttf",
            "software/study_runner/web/vendor/unlicensed/Other-Regular.woff2",
        ):
            with self.subTest(name=name), temporary_directory() as temporary:
                path = Path(temporary) / release.ARCHIVES[0]
                write_zip(path, members(name))

                with self.assertRaisesRegex(release.ReleaseError, "leaked"):
                    release.validate_archive(path, version=VERSION)

    def test_archive_carries_the_licensed_vendor_font(self) -> None:
        """Geist ships so a source build is not left on the system font stack."""
        with temporary_directory() as temporary:
            path = Path(temporary) / release.ARCHIVES[0]
            write_zip(path, members(
                "software/study_runner/web/vendor/geist/Geist-Regular.woff2",
                "software/study_runner/web/vendor/geist/Geist-SemiBold.woff2",
                "software/study_runner/web/vendor/geist/Geist-Bold.woff2",
            ))

            release.validate_archive(path, version=VERSION)

    def test_archive_rejects_parent_traversal(self) -> None:
        with temporary_directory() as temporary:
            path = Path(temporary) / release.ARCHIVES[0]
            write_zip(path, (*members(), f"{ROOT}/../outside.txt"))

            with self.assertRaisesRegex(release.ReleaseError, "unsafe path"):
                release.validate_archive(path, version=VERSION)

    def test_archive_rejects_symbolic_links(self) -> None:
        with temporary_directory() as temporary:
            path = Path(temporary) / release.ARCHIVES[0]
            write_zip(path, members())
            with zipfile.ZipFile(path, "a") as archive:
                link = zipfile.ZipInfo(f"{ROOT}/unsafe-link")
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, "../../outside")

            with self.assertRaisesRegex(release.ReleaseError, "symbolic link"):
                release.validate_archive(path, version=VERSION)

    def test_archive_rejects_windows_reserved_paths(self) -> None:
        with temporary_directory() as temporary:
            path = Path(temporary) / release.ARCHIVES[0]
            write_zip(path, (*members(), f"{ROOT}/CON"))

            with self.assertRaisesRegex(release.ReleaseError, "Windows-reserved"):
                release.validate_archive(path, version=VERSION)

    def test_changelog_section_must_exist_and_be_nonempty(self) -> None:
        changelog = "# Changelog\n\n## Unreleased\n\n## 1.2.3 - 2026-08-01\n\n- Stable.\n\n## 1.2.2\n\n- Older.\n"

        self.assertEqual(release.changelog_section(changelog, VERSION), "- Stable.")
        with self.assertRaisesRegex(release.ReleaseError, "no release section"):
            release.changelog_section(changelog, "9.9.9")
        with self.assertRaisesRegex(release.ReleaseError, "empty"):
            release.changelog_section("## 1.2.3\n\n## 1.2.2\n- Older.\n", VERSION)

    def test_repository_license_requires_proprietary_and_third_party_notices(self) -> None:
        valid = (
            "All rights reserved. This source is proprietary. "
            "Third-party components remain subject to their respective licenses. "
            "App-LabRecorder/XDFWriter"
        )

        release.validate_repository_license(valid)
        with self.assertRaisesRegex(release.ReleaseError, "required notice"):
            release.validate_repository_license("All rights reserved.")

    def test_third_party_notices_require_native_patch_and_license_provenance(self) -> None:
        notice = (
            "App-LabRecorder / XDFWriter native/src/xdfwriter_patched.cpp files "
            "remain derived works under xdfwriter/conversions.h and the Boost Software "
            "License 1.0 at software/recording_worker/native/vendor/BOOST_LICENSE_1_0.txt. "
            "Iconoir software/study_runner/web/vendor/iconoir/LICENSE. "
            "Geist software/study_runner/web/vendor/geist/LICENSE. The source archives "
            "do **not** contain facial_expression_model_weights.h5."
        )

        release.validate_third_party_notices(notice)
        with self.assertRaisesRegex(release.ReleaseError, "required provenance"):
            release.validate_third_party_notices("App-LabRecorder / XDFWriter")

        release.validate_third_party_license(
            "software/recording_worker/native/vendor/BOOST_LICENSE_1_0.txt",
            "Boost Software License - Version 1.0\nPermission is hereby granted",
        )
        release.validate_third_party_license(
            "software/study_runner/web/vendor/geist/LICENSE",
            "SIL OPEN FONT LICENSE Version 1.1\nPermission is hereby granted",
        )
        with self.assertRaisesRegex(release.ReleaseError, "required license text"):
            release.validate_third_party_license(
                "software/study_runner/web/vendor/iconoir/LICENSE", "MIT License"
            )
        with self.assertRaisesRegex(release.ReleaseError, "required license text"):
            release.validate_third_party_license(
                "software/study_runner/web/vendor/geist/LICENSE", "SIL OPEN FONT LICENSE Version 1.1"
            )

    def test_output_verification_is_fail_closed_and_declares_source_mode(self) -> None:
        with temporary_directory() as temporary:
            root = Path(temporary)
            paths = {name: root / name for name in release.ARCHIVES}
            write_zip(paths[release.ARCHIVES[0]], members())
            write_tar(paths[release.ARCHIVES[1]], members())
            artifacts = {
                name: {"sha256": release.sha256_file(path), "size": path.stat().st_size}
                for name, path in paths.items()
            }
            metadata = {
                "schema": release.SCHEMA,
                "release_kind": "source_server",
                "version": VERSION,
                "tag": f"app-v{VERSION}",
                "commit": "a" * 40,
                "repository": "owner/repository",
                "packaged_updater_compatible": False,
                "recording": {
                    "canonical_format": "XDF",
                    "native_core_bundled": False,
                    "local_setup_command": (
                        "python tools/setup_recording_worker.py --require-canonical"
                    ),
                    "supported_targets": list(release.SUPPORTED_RECORDING_TARGETS),
                    "linux_recording_supported": False,
                },
                "install": release.INSTALL_COMMANDS,
                "license": {
                    "identifier": "LicenseRef-Proprietary",
                    "name": "Proprietary - all rights reserved",
                    "file": "LICENSE",
                    "third_party_notices": list(release.THIRD_PARTY_NOTICE_FILES),
                },
                "artifacts": artifacts,
            }
            (root / "study-runner-source-release.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            checksum_targets = {
                **{name: artifacts[name]["sha256"] for name in release.ARCHIVES},
                "study-runner-source-release.json": release.sha256_file(
                    root / "study-runner-source-release.json"
                ),
            }
            (root / "SHA256SUMS").write_text(
                "".join(
                    f"{checksum_targets[name]}  {name}\n"
                    for name in sorted(checksum_targets)
                ),
                encoding="utf-8",
            )

            verified = release.verify_output(root)
            self.assertFalse(verified["packaged_updater_compatible"])
            self.assertFalse(verified["recording"]["native_core_bundled"])

            metadata["packaged_updater_compatible"] = True
            (root / "study-runner-source-release.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            with self.assertRaisesRegex(release.ReleaseError, "packaged-updater"):
                release.verify_output(root)

    def test_release_notes_include_install_commands_and_changelog(self) -> None:
        notes = release._release_notes(VERSION, "- Canonical recording architecture.")

        self.assertIn("install-windows.ps1", notes)
        self.assertIn("-InstallSystemDependencies", notes)
        self.assertIn("install-macos.sh", notes)
        self.assertIn("not prebuilt or bundled", notes)
        self.assertIn("THIRD_PARTY_NOTICES.md", notes)
        self.assertIn("Canonical recording architecture", notes)

    def test_workflow_publishes_only_source_release_assets(self) -> None:
        workflow = (release.REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("PyInstaller", workflow)
        self.assertNotIn("PYTHON_UPDATER_SIGNING_PRIVATE_KEY", workflow)
        self.assertNotIn("study-runner-python-latest.json", workflow)
        self.assertIn("study-runner-source.zip", workflow)
        self.assertIn("tools/install-windows.ps1", workflow)
        self.assertIn("tools/install-macos.sh", workflow)
        self.assertIn("macos-15-intel", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertRegex(
            workflow,
            r"(?ms)^  publish:.*?^    permissions:\n      contents: write$",
        )
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("Source regression suite (Linux, non-recording)", workflow)
        self.assertIn("python -m pytest", workflow)
        self.assertNotIn("ubuntu-latest", workflow)

        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                release.REPOSITORY_ROOT / ".github/workflows/release.yml",
                release.REPOSITORY_ROOT / ".github/workflows/ci.yml",
            )
        )
        action_refs = re.findall(r"uses:\s+actions/[^@\s]+@([^\s#]+)", workflows)
        self.assertTrue(action_refs)
        self.assertTrue(
            all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs),
            f"every GitHub Action must use an immutable commit SHA: {action_refs}",
        )
        self.assertEqual(workflows.count("persist-credentials: false"), 3)

    def test_maintainer_release_commands_do_not_build_packaged_assets(self) -> None:
        helper = (release.REPOSITORY_ROOT / "release_tools/release-study-runner.mjs").read_text(
            encoding="utf-8"
        )
        wrapper = (release.REPOSITORY_ROOT / "release.ps1").read_text(encoding="utf-8")

        full_checks = helper[helper.index("if (fullChecks)") : helper.index("git(['diff'")]
        self.assertIn("setup_recording_worker.py", full_checks)
        self.assertNotIn("build_python_onedir.py", full_checks)
        self.assertIn("promoteChangelog", helper)
        self.assertIn("verifyRemoteMainCommit", helper)
        self.assertIn("`${releaseCommit}:refs/heads/main`", helper)
        self.assertIn("['tag', '-a', tagName, intendedCommit", helper)
        self.assertNotIn("['tag', '-a', tagName, 'origin/main'", helper)
        self.assertIn("release-study-runner.mjs", wrapper)
        self.assertIn("No desktop bundle is built", wrapper)
        self.assertIn("exit $LASTEXITCODE", wrapper)


if __name__ == "__main__":
    unittest.main()
