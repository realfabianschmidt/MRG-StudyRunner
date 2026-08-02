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


SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SOFTWARE_ROOT.parent
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))

SETUP_PATH = REPOSITORY_ROOT / "tools" / "setup_recording_worker.py"
SETUP_SPEC = importlib.util.spec_from_file_location("recording_core_setup", SETUP_PATH)
assert SETUP_SPEC is not None and SETUP_SPEC.loader is not None
setup = importlib.util.module_from_spec(SETUP_SPEC)
SETUP_SPEC.loader.exec_module(setup)

from study_runner.backend.recording.worker_binary import (  # noqa: E402
    BUILD_MANIFEST_SCHEMA,
    BundledWorkerLocator,
    EXPECTED_UPSTREAM_COMMIT,
    EXPECTED_UPSTREAM_VERSION,
    EXPECTED_SOURCE_LOCK_SHA256,
)
from study_runner.recording_worker.core import (  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
