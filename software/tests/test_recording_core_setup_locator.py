from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SOFTWARE_ROOT.parent
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))

SETUP_PATH = REPOSITORY_ROOT / "tools" / "setup_recording_worker.py"
SETUP_SPEC = importlib.util.spec_from_file_location("recording_core_setup", SETUP_PATH)
assert SETUP_SPEC is not None and SETUP_SPEC.loader is not None
setup = importlib.util.module_from_spec(SETUP_SPEC)
SETUP_SPEC.loader.exec_module(setup)

from study_runner.data_core.host.worker_binary import (  # noqa: E402
    BUILD_MANIFEST_SCHEMA,
    BundledWorkerLocator,
    EXPECTED_UPSTREAM_COMMIT,
    EXPECTED_UPSTREAM_VERSION,
    EXPECTED_SOURCE_LOCK_SHA256,
)
from study_runner.data_core.worker.core import (  # noqa: E402
    CoreProbe,
    REQUIRED_CANONICAL_FEATURES,
)


def workspace_temporary_directory() -> tempfile.TemporaryDirectory[str]:
    root = SOFTWARE_ROOT / ".build" / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


def canonical_probe(path: Path, *, missing: str | None = None) -> CoreProbe:
    features = {name: True for name in REQUIRED_CANONICAL_FEATURES}
    if missing:
        features[missing] = False
    return CoreProbe(
        path=path.resolve(),
        abi_version=1,
        canonical_xdf=True,
        implementation="App-LabRecorder/XDFWriter",
        upstream_version=EXPECTED_UPSTREAM_VERSION,
        byte_order="little",
        features=features,
    )


def write_stage(
    stage: Path,
    *,
    platform_arch: str = "windows-x64",
    library_name: str = "xdf_core.dll",
    missing: str | None = None,
    tests_passed: bool = True,
) -> tuple[Path, CoreProbe]:
    stage.mkdir(parents=True)
    library = stage / library_name
    library.write_bytes(b"test native core")
    probe = canonical_probe(library, missing=missing)
    probe_payload = {
        "abi_version": probe.abi_version,
        "canonical_xdf": probe.canonical_xdf,
        "implementation": probe.implementation,
        "upstream_version": probe.upstream_version,
        "byte_order": probe.byte_order,
        "features": dict(probe.features),
    }
    status = "passed" if tests_passed else "skipped"
    manifest = {
        "schema": BUILD_MANIFEST_SCHEMA,
        "platform_arch": platform_arch,
        "core_library": library_name,
        "core_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "canonical_xdf": missing is None,
        "source_lock_sha256": EXPECTED_SOURCE_LOCK_SHA256,
        "probe": probe_payload,
        "tests": {"ctest": status, "synthetic_xdf_smoke": status},
        "upstream": {
            "tag": EXPECTED_UPSTREAM_VERSION,
            "commit": EXPECTED_UPSTREAM_COMMIT,
            "source_lock_sha256": EXPECTED_SOURCE_LOCK_SHA256,
        },
    }
    (stage / "worker-build.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return library, probe


class RecordingCoreSetupTests(unittest.TestCase):
    def test_supported_targets_include_macos_apple_silicon(self) -> None:
        windows = setup.supported_target("Windows", "AMD64")
        apple_silicon = setup.supported_target("Darwin", "arm64")

        self.assertEqual(windows["platform_arch"], "windows-x64")
        self.assertEqual(windows["library_name"], "xdf_core.dll")
        self.assertEqual(apple_silicon["platform_arch"], "macos-arm64")
        self.assertEqual(apple_silicon["library_name"], "libxdf_core.dylib")
        self.assertEqual(apple_silicon["cmake_architecture"], "arm64")

    def test_vendored_upstream_matches_reviewed_lock(self) -> None:
        verified = setup.verify_upstream_sources()

        self.assertEqual(verified["tag"], "v1.17.1")
        self.assertEqual(verified["commit"], setup.EXPECTED_UPSTREAM_COMMIT)
        self.assertEqual(verified["source_lock_sha256"], setup.EXPECTED_SOURCE_LOCK_SHA256)
        self.assertEqual(verified["files"], setup.EXPECTED_UPSTREAM_HASHES)

    def test_upstream_verifier_rejects_modified_source(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            native = Path(temp_dir) / "native"
            source_native = SOFTWARE_ROOT / "recording_worker" / "native"
            shutil.copytree(source_native / "vendor", native / "vendor")
            shutil.copy2(source_native / "UPSTREAM_LOCK.json", native / "UPSTREAM_LOCK.json")
            changed = native / "vendor/App-LabRecorder/xdfwriter/xdfwriter.h"
            changed.write_text(changed.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8")

            with self.assertRaisesRegex(setup.SetupError, "differs from App-LabRecorder"):
                setup.verify_upstream_sources(native)

    def test_linux_rejection_happens_before_output_directories_are_created(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            build_dir = root / "build"
            stage_dir = root / "stage"
            arguments = argparse.Namespace(
                build_dir=build_dir,
                stage_dir=stage_dir,
                configuration="Release",
                skip_tests=False,
                probe_only=False,
                json=True,
                require_canonical=True,
            )
            with mock.patch.object(setup.platform, "system", return_value="Linux"), mock.patch.object(
                setup.platform, "machine", return_value="x86_64"
            ):
                with self.assertRaisesRegex(setup.SetupError, "intentionally unavailable"):
                    setup.run(arguments)

            self.assertFalse(build_dir.exists())
            self.assertFalse(stage_dir.exists())

    def test_macos_toolchain_accepts_command_line_tools_and_compile_tests_cstdint(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            developer_dir = root / "CommandLineTools"
            compiler = developer_dir / "usr/bin/clang++"
            sdk = developer_dir / "SDKs/MacOSX.sdk"
            compiler.parent.mkdir(parents=True)
            compiler.write_bytes(b"compiler")
            sdk.mkdir(parents=True)
            commands: list[list[str]] = []

            def fake_run_command(command, *, quiet):
                command = [str(part) for part in command]
                commands.append(command)
                if command[-2:] == ["--find", "clang++"]:
                    return f"{compiler}\n"
                if command[-1:] == ["--show-sdk-path"]:
                    return f"{sdk}\n"
                if command == ["xcode-select", "--print-path"]:
                    return f"{developer_dir}\n"
                source = Path(command[-1])
                self.assertIn("#include <cstdint>", source.read_text(encoding="utf-8"))
                return ""

            with mock.patch.object(setup, "_run_command", side_effect=fake_run_command):
                toolchain = setup._macos_toolchain(setup.supported_target("Darwin", "x86_64"))

            self.assertEqual(toolchain["compiler"], str(compiler))
            self.assertEqual(toolchain["sdk_path"], str(sdk))
            self.assertEqual(toolchain["developer_dir"], str(developer_dir))
            compile_call = commands[-1]
            self.assertIn("-std=c++20", compile_call)
            self.assertIn("x86_64", compile_call)
            self.assertFalse(any(c[:1] == ["xcodebuild"] for c in commands))

    def test_macos_toolchain_explains_a_missing_compiler(self) -> None:
        with mock.patch.object(
            setup, "_run_command", side_effect=setup.SetupError("xcrun: error: invalid active developer path")
        ):
            with self.assertRaisesRegex(setup.SetupError, "xcode-select --install"):
                setup._macos_toolchain(setup.supported_target("Darwin", "arm64"))

    def test_macos_toolchain_classifies_cstdint_compile_failure(self) -> None:
        def fake_run_command(command, *, quiet):
            if command[-2:] == ["--find", "clang++"]:
                return "/Library/Developer/CommandLineTools/usr/bin/clang++\n"
            if command[-1:] == ["--show-sdk-path"]:
                return "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk\n"
            if command == ["xcode-select", "--print-path"]:
                return "/Library/Developer/CommandLineTools\n"
            raise setup.SetupError("fatal error: 'cstdint' file not found")

        with mock.patch.object(setup, "_run_command", side_effect=fake_run_command), mock.patch.object(
            setup.Path, "is_file", return_value=True
        ), mock.patch.object(setup.Path, "is_dir", return_value=True):
            with self.assertRaisesRegex(setup.SetupError, r"C\+\+20 toolchain.*cstdint"):
                setup._macos_toolchain(setup.supported_target("Darwin", "arm64"))

    def test_macos_configure_command_includes_compiler_and_sysroot(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            build_dir = root / "build"
            stage_dir = root / "stage"
            target = setup.supported_target("Darwin", "arm64")
            commands: list[list[str]] = []

            def fake_run_command(command, *, quiet):
                command = [str(part) for part in command]
                commands.append(command)
                if "-S" in command:
                    return ""  # configure "succeeded"
                # Stop right after configure -- only that call matters here.
                raise setup.SetupError("stop here after configure")

            with mock.patch.object(setup, "_run_command", side_effect=fake_run_command), \
                mock.patch.object(setup.shutil, "which", return_value="/usr/bin/cmake"), \
                mock.patch.object(
                    setup, "verify_upstream_sources", return_value={"source_lock_sha256": "x"}
                ), \
                mock.patch.object(
                    setup,
                    "_macos_toolchain",
                    return_value={
                        "developer_dir": "/Library/Developer/CommandLineTools",
                        "compiler": "/Library/Developer/CommandLineTools/usr/bin/clang++",
                        "sdk_path": "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk",
                    },
                ):
                with self.assertRaisesRegex(setup.SetupError, "stop here after configure"):
                    setup.build_core(
                        target=target,
                        build_dir=build_dir,
                        stage_dir=stage_dir,
                        configuration="Release",
                        skip_tests=True,
                        require_canonical=False,
                        quiet=True,
                    )

            configure_calls = [c for c in commands if c[:1] == ["cmake"] and "-S" in c]
            self.assertEqual(len(configure_calls), 1)
            self.assertIn(
                "-DCMAKE_CXX_COMPILER=/Library/Developer/CommandLineTools/usr/bin/clang++",
                configure_calls[0],
            )
            self.assertIn(
                "-DCMAKE_OSX_SYSROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk",
                configure_calls[0],
            )

    def test_stale_macos_cache_is_only_reset_for_the_allowed_generated_dir(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            build_dir = root / "build"
            build_dir.mkdir()
            cache = build_dir / "CMakeCache.txt"
            cache.write_text(
                "CMAKE_CXX_COMPILER:FILEPATH=/Library/Developer/CommandLineTools/usr/bin/c++\n"
                "CMAKE_OSX_SYSROOT:PATH=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk\n",
                encoding="utf-8",
            )
            toolchain = {
                "compiler": "/Applications/Xcode.app/clang++",
                "sdk_path": "/Applications/Xcode.app/MacOSX.sdk",
            }

            with self.assertRaisesRegex(setup.SetupError, "custom build directory"):
                setup._reset_stale_macos_cache(
                    build_dir,
                    toolchain,
                    resettable_build_dir=None,
                )
            self.assertTrue(cache.is_file())

            reset = setup._reset_stale_macos_cache(
                build_dir,
                toolchain,
                resettable_build_dir=build_dir,
            )
            self.assertTrue(reset)
            self.assertFalse(build_dir.exists())

    def test_probe_only_verifies_manifest_hash_and_canonical_features(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            stage = Path(temp_dir) / "stage"
            library, _probe = write_stage(stage)
            raw_probe = json.loads((stage / "worker-build.json").read_text(encoding="utf-8"))["probe"]

            with mock.patch.object(setup, "probe_core_library", return_value=raw_probe):
                result = setup.probe_stage(
                    stage,
                    setup.supported_target("Windows", "AMD64"),
                    require_canonical=True,
                )

            self.assertTrue(result["canonical_xdf"])
            self.assertEqual(Path(result["core_library"]), library.resolve())


class RecordingCoreLocatorTests(unittest.TestCase):
    def test_environment_core_precedes_the_local_stage(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            local_stage = root / ".build/xdf_core/windows-x64"
            environment_stage = root / "environment-stage"
            write_stage(local_stage)
            environment_library, environment_probe = write_stage(environment_stage)
            probes: list[Path] = []

            def probe(path: Path) -> CoreProbe:
                probes.append(path)
                return environment_probe

            status = BundledWorkerLocator(
                root,
                environment={"STUDY_RUNNER_XDF_CORE": str(environment_stage)},
                system_name="Windows",
                machine_name="AMD64",
                core_probe=probe,
            ).locate()

            self.assertTrue(status.available)
            self.assertTrue(status.canonical_xdf)
            self.assertEqual(status.kind, "hybrid_core")
            self.assertEqual(status.core_path, environment_library.resolve())
            self.assertEqual(probes, [environment_library.resolve()])

    def test_noncanonical_core_is_never_available(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            stage = root / ".build/xdf_core/windows-x64"
            _library, probe = write_stage(stage, missing="lossless_merge")

            status = BundledWorkerLocator(
                root,
                environment={},
                system_name="Windows",
                machine_name="x86_64",
                core_probe=lambda _path: probe,
            ).locate()

            self.assertFalse(status.available)
            self.assertFalse(status.canonical_xdf)
            self.assertIn("lossless_merge", status.reason or "")

    def test_stage_with_skipped_native_tests_is_not_available(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            stage = root / ".build/xdf_core/windows-x64"
            _library, probe = write_stage(stage, tests_passed=False)

            status = BundledWorkerLocator(
                root,
                environment={},
                system_name="Windows",
                machine_name="AMD64",
                core_probe=lambda _path: probe,
            ).locate()

            self.assertFalse(status.available)
            self.assertIn("synthetic XDF smoke", status.reason or "")

    def test_changed_source_lock_fingerprint_is_not_available(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            stage = root / ".build/xdf_core/windows-x64"
            _library, probe = write_stage(stage)
            manifest_path = stage / "worker-build.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_lock_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            status = BundledWorkerLocator(
                root,
                environment={},
                system_name="Windows",
                machine_name="AMD64",
                core_probe=lambda _path: probe,
            ).locate()

            self.assertFalse(status.available)
            self.assertIn("source lock", status.reason or "")

    def test_linux_core_discovery_is_fail_closed_without_loading_library(self) -> None:
        calls: list[Path] = []
        status = BundledWorkerLocator(
            Path("."),
            environment={"STUDY_RUNNER_XDF_CORE": "untrusted.so"},
            system_name="Linux",
            machine_name="x86_64",
            core_probe=lambda path: calls.append(path) or canonical_probe(path),
        ).locate()

        self.assertFalse(status.available)
        self.assertIn("Linux", status.reason or "")
        self.assertEqual(calls, [])

    def test_explicit_legacy_worker_remains_a_distinct_test_injection(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            worker = Path(temp_dir) / "fake-worker"
            worker.write_bytes(b"test worker")

            status = BundledWorkerLocator(
                Path(temp_dir),
                configured_path=worker,
                environment={},
                system_name="Linux",
                machine_name="x86_64",
            ).locate()

            self.assertTrue(status.available)
            self.assertEqual(status.kind, "legacy_external_worker")
            self.assertFalse(status.canonical_xdf)
            self.assertIsNone(status.core_path)



def write_core_asset(
    path: Path,
    *,
    platform_arch: str = "windows-x64",
    library_name: str = "xdf_core.dll",
    native_source: str | None = None,
    extra_member: str | None = None,
) -> tuple[str, dict]:
    """Write a prebuilt-core zip exactly like ``--package`` does and return its hash."""

    library = b"test native core"
    probe = canonical_probe(Path(library_name))
    manifest = {
        "schema": BUILD_MANIFEST_SCHEMA,
        "platform_arch": platform_arch,
        "core_library": library_name,
        "core_sha256": hashlib.sha256(library).hexdigest(),
        "canonical_xdf": True,
        "source_lock_sha256": EXPECTED_SOURCE_LOCK_SHA256,
        "native_source_sha256": native_source or setup.native_source_sha256(),
        "source": "local_build",
        "probe": {
            "abi_version": probe.abi_version,
            "canonical_xdf": probe.canonical_xdf,
            "implementation": probe.implementation,
            "upstream_version": probe.upstream_version,
            "byte_order": probe.byte_order,
            "features": dict(probe.features),
        },
        "tests": {"ctest": "passed", "synthetic_xdf_smoke": "passed"},
        "upstream": {
            "tag": EXPECTED_UPSTREAM_VERSION,
            "commit": EXPECTED_UPSTREAM_COMMIT,
            "source_lock_sha256": EXPECTED_SOURCE_LOCK_SHA256,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(library_name, library)
        archive.writestr("worker-build.json", json.dumps(manifest))
        if extra_member:
            archive.writestr(extra_member, b"unexpected")
    return hashlib.sha256(path.read_bytes()).hexdigest(), manifest


class PrebuiltCoreTests(unittest.TestCase):
    target = setup.supported_target("Windows", "AMD64")

    def install(self, root: Path, asset: Path, checksum: str, **overrides):
        checksums = root / "SHA256SUMS"
        checksums.write_text(f"{checksum}  {asset.name}\n", encoding="utf-8")
        manifest = json.loads(zipfile.ZipFile(asset).read("worker-build.json"))
        with mock.patch.object(
            setup, "probe_core_library", return_value=manifest["probe"]
        ), mock.patch.object(
            setup, "run_synthetic_xdf_smoke", return_value={"status": "passed"}
        ) as smoke:
            result = setup.install_prebuilt(
                asset,
                target=overrides.get("target", self.target),
                build_dir=root / "build",
                stage_dir=root / "stage",
                checksums_path=checksums,
                release_info=overrides.get("release_info"),
            )
        return result, smoke

    def test_native_source_fingerprint_ignores_line_endings_and_hidden_files(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            unix = Path(temp_dir) / "unix"
            windows = Path(temp_dir) / "windows"
            for root, newline in ((unix, "\n"), (windows, "\r\n")):
                (root / "src").mkdir(parents=True)
                (root / "src" / "core.cpp").write_bytes(f"int x;{newline}int y;{newline}".encode())
            (windows / ".DS_Store").write_bytes(b"finder noise")

            self.assertEqual(setup.native_source_sha256(unix), setup.native_source_sha256(windows))
            (unix / "src" / "core.cpp").write_bytes(b"int x;\nint z;\n")
            self.assertNotEqual(setup.native_source_sha256(unix), setup.native_source_sha256(windows))

    def test_prebuilt_core_is_verified_tested_locally_and_staged(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            asset = root / setup.core_asset_name("windows-x64")
            checksum, _manifest = write_core_asset(asset)

            result, smoke = self.install(root, asset, checksum)

            smoke.assert_called_once()
            self.assertEqual(result["mode"], "install_prebuilt")
            self.assertTrue(result["canonical_xdf"])
            staged = json.loads((root / "stage" / "worker-build.json").read_text(encoding="utf-8"))
            self.assertEqual(staged["source"], "prebuilt")
            self.assertEqual(staged["asset_sha256"], checksum)
            self.assertEqual(staged["tests"]["local_synthetic_xdf_smoke"], "passed")
            self.assertEqual(staged["tests"]["ctest"], "passed")
            self.assertEqual(list((root / "build").iterdir()), [])

    def test_prebuilt_core_with_wrong_checksum_is_never_staged(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            asset = root / setup.core_asset_name("windows-x64")
            write_core_asset(asset)

            with self.assertRaisesRegex(setup.SetupError, "checksum mismatch"):
                self.install(root, asset, "0" * 64)
            self.assertFalse((root / "stage").exists())

    def test_prebuilt_core_for_other_native_sources_is_rejected(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            asset = root / setup.core_asset_name("windows-x64")
            checksum, _manifest = write_core_asset(asset, native_source="f" * 64)

            with self.assertRaisesRegex(setup.SetupError, "different native sources"):
                self.install(root, asset, checksum)
            self.assertFalse((root / "stage").exists())

    def test_prebuilt_core_for_another_platform_is_rejected(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            asset = root / setup.core_asset_name("windows-x64")
            checksum, _manifest = write_core_asset(
                asset, platform_arch="macos-x64", library_name="xdf_core.dll"
            )

            with self.assertRaisesRegex(setup.SetupError, "does not match this host"):
                self.install(root, asset, checksum)

    def test_prebuilt_core_with_unexpected_members_is_rejected(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            asset = root / setup.core_asset_name("windows-x64")
            checksum, _manifest = write_core_asset(asset, extra_member="../escape.dll")

            with self.assertRaisesRegex(setup.SetupError, "must contain exactly"):
                self.install(root, asset, checksum)

    def test_release_description_is_the_trust_anchor_inside_an_archive(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            asset = root / setup.core_asset_name("windows-x64")
            checksum, _manifest = write_core_asset(asset)
            release_info = {
                "schema": setup.RELEASE_INFO_SCHEMA,
                "repository": "owner/repository",
                "tag": "app-v1.2.3",
                "native_cores": {
                    "windows-x64": {"asset": asset.name, "sha256": "e" * 64},
                },
            }

            # A matching SHA256SUMS next to the download must not override the
            # hash that arrived inside the source archive.
            with self.assertRaisesRegex(setup.SetupError, "checksum mismatch"):
                self.install(root, asset, checksum, release_info=release_info)

            release_info["native_cores"]["windows-x64"]["sha256"] = checksum
            result, _smoke = self.install(root, asset, "0" * 64, release_info=release_info)
            self.assertTrue(result["canonical_xdf"])

    def test_prebuilt_source_uses_the_archive_tag_or_the_latest_release(self) -> None:
        tagged = setup.prebuilt_source(
            self.target,
            {
                "repository": "owner/repository",
                "tag": "app-v1.2.3",
                "native_cores": {"windows-x64": {}},
            },
        )
        latest = setup.prebuilt_source(self.target, None)

        self.assertEqual(
            tagged["url"],
            "https://github.com/owner/repository/releases/download/app-v1.2.3/"
            "study-runner-xdf-core-windows-x64.zip",
        )
        self.assertTrue(latest["url"].startswith("https://github.com/"))
        self.assertIn("/releases/latest/download/", latest["url"])
        self.assertTrue(latest["checksums_url"].endswith("/SHA256SUMS"))

    def test_package_refuses_a_core_without_passed_tests(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            stage = Path(temp_dir) / "stage"
            write_stage(stage, tests_passed=False)
            raw_probe = json.loads((stage / "worker-build.json").read_text(encoding="utf-8"))["probe"]

            with mock.patch.object(setup, "probe_core_library", return_value=raw_probe):
                with self.assertRaisesRegex(setup.SetupError, "CTest and synthetic XDF smoke"):
                    setup.package_stage(stage, self.target, Path(temp_dir) / "core.zip")

    def test_package_output_installs_as_a_prebuilt_core(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            stage = root / "built"
            write_stage(stage)
            manifest_path = stage / "worker-build.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["native_source_sha256"] = setup.native_source_sha256()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            asset = root / setup.core_asset_name("windows-x64")

            with mock.patch.object(setup, "probe_core_library", return_value=manifest["probe"]):
                packaged = setup.package_stage(stage, self.target, asset)

            with zipfile.ZipFile(asset) as archive:
                self.assertEqual(
                    sorted(info.filename for info in archive.infolist()),
                    ["worker-build.json", "xdf_core.dll"],
                )
            result, _smoke = self.install(root, asset, packaged["asset_sha256"])
            self.assertTrue(result["canonical_xdf"])


if __name__ == "__main__":
    unittest.main()
