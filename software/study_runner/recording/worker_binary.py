"""Fail-closed discovery for the hybrid recording worker's native XDF core.

Canonical discovery never searches ``PATH``. A source checkout uses the exact
generated stage below ``software/.build``; packaged resources can be added as
an explicit candidate when release bundling is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import platform
from typing import Any, Callable, Mapping

from study_runner.recording_worker.core import CoreProbe, NativeXdfError, probe_core_library

from .worker_protocol import WORKER_PROTOCOL_VERSION


CORE_ENVIRONMENT_VARIABLE = "STUDY_RUNNER_XDF_CORE"
BUILD_MANIFEST_SCHEMA = "study-runner/xdf-core-build/v1"
EXPECTED_UPSTREAM_VERSION = "v1.17.1"
EXPECTED_UPSTREAM_COMMIT = "8419550553e4336dd46378a9a871b3065a70b895"
EXPECTED_SOURCE_LOCK_SHA256 = "c4f344ef4bf8b94580cc8d096aa3d6efaf1bf0f73c6598a2067ba26051fd0c76"


@dataclass(frozen=True)
class WorkerBinaryAvailability:
    available: bool
    path: Path | None
    protocol_version: int
    reason: str | None = None
    kind: str | None = None
    core_path: Path | None = None
    canonical_xdf: bool = False
    supports_merge: bool = False
    probe: Mapping[str, Any] = field(default_factory=dict)
    build_manifest: Mapping[str, Any] = field(default_factory=dict)


class BundledWorkerLocator:
    """Locate a verified native core or an explicit legacy test worker.

    ``configured_path`` is retained only as a compatibility seam for the
    pre-hybrid external-worker tests. It is not a canonical-core candidate and
    is never inferred from ``PATH``.
    """

    def __init__(
        self,
        resource_root: Path,
        *,
        configured_path: Path | None = None,
        environment: Mapping[str, str] | None = None,
        system_name: str | None = None,
        machine_name: str | None = None,
        core_probe: Callable[[Path], CoreProbe] = probe_core_library,
    ) -> None:
        self.resource_root = Path(resource_root).resolve()
        self.configured_path = Path(configured_path).expanduser() if configured_path else None
        self.environment = os.environ if environment is None else environment
        self.system_name = system_name
        self.machine_name = machine_name
        self._core_probe = core_probe

    def locate(self) -> WorkerBinaryAvailability:
        if self.configured_path is not None:
            return self._locate_legacy_worker()

        target, unsupported_reason = _core_target(self.system_name, self.machine_name)
        if target is None:
            return self._unavailable(unsupported_reason or "unsupported recording platform")

        configured_core = str(self.environment.get(CORE_ENVIRONMENT_VARIABLE, "")).strip()
        if configured_core:
            candidate = Path(configured_core).expanduser()
            if not candidate.is_absolute():
                candidate = self.resource_root / candidate
            candidate = _library_from_candidate(candidate, target["library_name"])
            return self._probe_candidate(candidate, target, source="environment")

        candidate = (
            self.resource_root
            / ".build"
            / "xdf_core"
            / target["platform_arch"]
            / target["library_name"]
        )
        if not candidate.is_file():
            return self._unavailable(
                "canonical native recording core not found "
                f"(checked: {candidate.resolve()}); run tools/setup_recording_worker.py"
            )
        return self._probe_candidate(candidate, target, source="local_stage")

    def _locate_legacy_worker(self) -> WorkerBinaryAvailability:
        candidate = self.configured_path
        assert candidate is not None
        if not candidate.is_absolute():
            candidate = self.resource_root / candidate
        resolved = candidate.resolve()
        if not resolved.is_file():
            return self._unavailable(f"configured legacy recording worker not found: {resolved}")
        return WorkerBinaryAvailability(
            available=True,
            path=resolved,
            protocol_version=WORKER_PROTOCOL_VERSION,
            kind="legacy_external_worker",
            reason=(
                "legacy external worker compatibility path; canonical core guarantees are "
                "validated only after migration to the hybrid worker"
            ),
        )

    def _probe_candidate(
        self,
        candidate: Path,
        target: Mapping[str, str],
        *,
        source: str,
    ) -> WorkerBinaryAvailability:
        resolved = candidate.resolve()
        if not resolved.is_file():
            return self._unavailable(f"configured native recording core not found: {resolved}")
        try:
            manifest = _read_build_manifest(resolved, target)
            probe = self._core_probe(resolved)
            reason = _canonical_failure_reason(probe, manifest)
        except (NativeXdfError, OSError, ValueError) as error:
            return self._unavailable(
                f"native recording core validation failed ({source}): {error}",
                path=resolved,
                core_path=resolved,
                kind="hybrid_core",
            )
        if reason:
            return self._unavailable(
                f"native recording core is not canonical ({source}): {reason}",
                path=resolved,
                core_path=resolved,
                kind="hybrid_core",
                probe=probe.as_dict(),
                build_manifest=manifest,
            )
        return WorkerBinaryAvailability(
            available=True,
            path=resolved,
            protocol_version=WORKER_PROTOCOL_VERSION,
            kind="hybrid_core",
            core_path=resolved,
            canonical_xdf=True,
            supports_merge=True,
            probe=probe.as_dict(),
            build_manifest=manifest,
        )

    @staticmethod
    def _unavailable(
        reason: str,
        *,
        path: Path | None = None,
        core_path: Path | None = None,
        kind: str | None = None,
        probe: Mapping[str, Any] | None = None,
        build_manifest: Mapping[str, Any] | None = None,
    ) -> WorkerBinaryAvailability:
        return WorkerBinaryAvailability(
            available=False,
            path=path,
            protocol_version=WORKER_PROTOCOL_VERSION,
            reason=reason,
            kind=kind,
            core_path=core_path,
            probe=dict(probe or {}),
            build_manifest=dict(build_manifest or {}),
        )


def _core_target(
    system_name: str | None,
    machine_name: str | None,
) -> tuple[dict[str, str] | None, str | None]:
    system = (system_name or platform.system()).strip().casefold()
    machine = (machine_name or platform.machine()).strip().casefold()
    if system == "linux":
        return None, (
            "Linux recording is intentionally fail-closed until a canonical XDF core passes "
            "the Linux release gate"
        )
    if system == "windows":
        if machine not in {"amd64", "x86_64"}:
            return None, "canonical recording currently requires Windows x64"
        return {
            "platform_arch": "windows-x64",
            "library_name": "xdf_core.dll",
        }, None
    if system == "darwin":
        if machine in {"amd64", "x86_64"}:
            architecture = "x64"
        elif machine in {"arm64", "aarch64"}:
            architecture = "arm64"
        else:
            return None, f"canonical recording does not support macOS architecture {machine!r}"
        return {
            "platform_arch": f"macos-{architecture}",
            "library_name": "libxdf_core.dylib",
        }, None
    return None, f"canonical recording does not support operating system {system!r}"


def _library_from_candidate(candidate: Path, library_name: str) -> Path:
    return candidate / library_name if candidate.is_dir() else candidate


def _read_build_manifest(library_path: Path, target: Mapping[str, str]) -> dict[str, Any]:
    manifest_path = library_path.parent / "worker-build.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"worker-build.json is missing or unreadable beside {library_path.name}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != BUILD_MANIFEST_SCHEMA:
        raise ValueError("worker-build.json has an unsupported schema")
    if manifest.get("platform_arch") != target["platform_arch"]:
        raise ValueError("worker-build.json targets a different platform or architecture")
    if manifest.get("core_library") != library_path.name or library_path.name != target["library_name"]:
        raise ValueError("worker-build.json names an unexpected core library")
    actual_hash = hashlib.sha256(library_path.read_bytes()).hexdigest()
    if manifest.get("core_sha256") != actual_hash:
        raise ValueError("native core SHA-256 differs from worker-build.json")
    upstream = manifest.get("upstream")
    if not isinstance(upstream, Mapping):
        raise ValueError("worker-build.json has no upstream provenance")
    if upstream.get("tag") != EXPECTED_UPSTREAM_VERSION:
        raise ValueError("worker-build.json has an unexpected App-LabRecorder version")
    if upstream.get("commit") != EXPECTED_UPSTREAM_COMMIT:
        raise ValueError("worker-build.json has an unexpected App-LabRecorder commit")
    if upstream.get("source_lock_sha256") != EXPECTED_SOURCE_LOCK_SHA256:
        raise ValueError("worker-build.json has an unexpected upstream source-lock fingerprint")
    if manifest.get("source_lock_sha256") != EXPECTED_SOURCE_LOCK_SHA256:
        raise ValueError("worker-build.json does not bind the build to its upstream source lock")
    return manifest


def _canonical_failure_reason(probe: CoreProbe, manifest: Mapping[str, Any]) -> str | None:
    if probe.abi_version != 1:
        return f"unsupported native ABI {probe.abi_version}"
    if probe.upstream_version != EXPECTED_UPSTREAM_VERSION:
        return f"unexpected App-LabRecorder version {probe.upstream_version!r}"
    if not probe.usable:
        missing = ", ".join(probe.missing_features)
        return missing or "probe rejected canonical flag or byte order"
    manifest_probe = manifest.get("probe")
    if not isinstance(manifest_probe, Mapping):
        return "worker-build.json has no native probe"
    expected_probe = {
        "abi_version": probe.abi_version,
        "canonical_xdf": probe.canonical_xdf,
        "implementation": probe.implementation,
        "upstream_version": probe.upstream_version,
        "byte_order": probe.byte_order,
        "features": dict(probe.features),
    }
    if any(manifest_probe.get(key) != value for key, value in expected_probe.items()):
        return "live native probe differs from worker-build.json"
    if manifest.get("canonical_xdf") is not True:
        return "worker-build.json does not declare canonical_xdf=true"
    tests = manifest.get("tests")
    if not isinstance(tests, Mapping):
        return "worker-build.json has no test results"
    if tests.get("ctest") != "passed" or tests.get("synthetic_xdf_smoke") != "passed":
        return "CTest and the synthetic XDF smoke must both pass"
    return None
