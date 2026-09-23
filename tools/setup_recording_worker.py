#!/usr/bin/env python3
"""Install, verify, or build the platform-native XDF core used by the recording worker.

The helper itself never downloads anything. Installers fetch the prebuilt,
CI-tested core and pass it to ``--install-prebuilt``, which checks its SHA-256,
its native-source fingerprint, and runs the synthetic XDF smoke on this machine.
Source builds accept only the pinned files below
``software/recording_worker/native/vendor``.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOFTWARE_ROOT = REPOSITORY_ROOT / "software"
NATIVE_SOURCE_DIR = SOFTWARE_ROOT / "recording_worker" / "native"
UPSTREAM_LOCK_PATH = NATIVE_SOURCE_DIR / "UPSTREAM_LOCK.json"
BUILD_MANIFEST_SCHEMA = "study-runner/xdf-core-build/v1"
UPSTREAM_LOCK_SCHEMA = "study-runner/xdf-core-upstream-lock/v1"
CORE_ABI_VERSION = 1
# Written into release archives by release_tools/build_source_release.py. A Git
# checkout has no such file and falls back to the latest published release.
RELEASE_INFO_PATH = REPOSITORY_ROOT / "study-runner-release.json"
RELEASE_INFO_SCHEMA = "study-runner/release-info/v1"
DEFAULT_RELEASE_REPOSITORY = "realfabianschmidt/MRG-StudyRunner"
CORE_ASSET_PREFIX = "study-runner-xdf-core-"
EXPECTED_UPSTREAM_TAG = "v1.17.1"
EXPECTED_UPSTREAM_COMMIT = "8419550553e4336dd46378a9a871b3065a70b895"
EXPECTED_SOURCE_LOCK_SHA256 = "c4f344ef4bf8b94580cc8d096aa3d6efaf1bf0f73c6598a2067ba26051fd0c76"
EXPECTED_UPSTREAM_HASHES = {
    "vendor/App-LabRecorder/LICENSE": (
        "ba6a7531ca31869c4f4909448552323f12c3c74d1ed38aff68313b9536d591dc"
    ),
    "vendor/App-LabRecorder/xdfwriter/conversions.h": (
        "33a5de1a2851aa0adc1d7f7e1b3b8b6b2448f73d46102f2c5951e691d2b66e13"
    ),
    "vendor/App-LabRecorder/xdfwriter/xdfwriter.cpp": (
        "82a11d1290ff441c641fd550c3937a45e84ee81557c9ff00c6ecbb30ef3c3979"
    ),
    "vendor/App-LabRecorder/xdfwriter/xdfwriter.h": (
        "81852d7dc1cc5d40c9ef31d1d5af93310877f17db6c5289d060db8eb9001d646"
    ),
}
REQUIRED_CANONICAL_FEATURES = (
    "typed_batches",
    "string_batches",
    "clock_offsets",
    "boundaries",
    "exclusive_create",
    "durable_flush",
    "checked_raw_chunks",
    "lossless_merge",
)


class SetupError(RuntimeError):
    """The native core cannot be built or trusted."""


def supported_target(
    system_name: str | None = None,
    machine_name: str | None = None,
) -> dict[str, str]:
    """Return the supported native target without touching the filesystem."""

    system = (system_name or platform.system()).strip().casefold()
    machine = (machine_name or platform.machine()).strip().casefold()
    if system == "linux":
        raise SetupError(
            "Linux recording is intentionally unavailable: no canonical XDF core is released "
            "for Linux yet"
        )
    if system == "windows":
        if machine not in {"amd64", "x86_64"}:
            raise SetupError("Windows recording currently requires an x64 host")
        return {
            "system": "windows",
            "architecture": "x64",
            "platform_arch": "windows-x64",
            "library_name": "xdf_core.dll",
            "cmake_architecture": "x64",
        }
    if system == "darwin":
        if machine in {"amd64", "x86_64"}:
            architecture = "x64"
            cmake_architecture = "x86_64"
        elif machine in {"arm64", "aarch64"}:
            architecture = "arm64"
            cmake_architecture = "arm64"
        else:
            raise SetupError(f"macOS recording does not support architecture {machine!r}")
        return {
            "system": "macos",
            "architecture": architecture,
            "platform_arch": f"macos-{architecture}",
            "library_name": "libxdf_core.dylib",
            "cmake_architecture": cmake_architecture,
        }
    raise SetupError(f"recording core setup does not support operating system {system!r}")


def verify_upstream_sources(native_source_dir: Path = NATIVE_SOURCE_DIR) -> dict[str, Any]:
    """Verify the embedded upstream identity and every pristine source file."""

    native_source_dir = Path(native_source_dir).resolve()
    lock_path = native_source_dir / "UPSTREAM_LOCK.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SetupError(f"upstream lock is unreadable: {lock_path}: {error}") from error
    if not isinstance(lock, dict) or lock.get("schema") != UPSTREAM_LOCK_SCHEMA:
        raise SetupError("upstream lock has an unsupported schema")
    if lock.get("tag") != EXPECTED_UPSTREAM_TAG:
        raise SetupError(f"upstream lock must pin {EXPECTED_UPSTREAM_TAG}")
    if lock.get("commit") != EXPECTED_UPSTREAM_COMMIT:
        raise SetupError(f"upstream lock must pin commit {EXPECTED_UPSTREAM_COMMIT}")
    if lock.get("hash_normalization") != "utf8-lines-to-crlf":
        raise SetupError("upstream lock has an unsupported hash normalization")
    declared_hashes = lock.get("files")
    if declared_hashes != EXPECTED_UPSTREAM_HASHES:
        raise SetupError("upstream lock hashes do not match the reviewed v1.17.1 baseline")
    canonical_lock = json.dumps(
        lock,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    source_lock_sha256 = hashlib.sha256(canonical_lock).hexdigest()
    if source_lock_sha256 != EXPECTED_SOURCE_LOCK_SHA256:
        raise SetupError("upstream lock fingerprint differs from the reviewed v1.17.1 lock")

    verified: dict[str, str] = {}
    for relative_path, expected_hash in EXPECTED_UPSTREAM_HASHES.items():
        source_path = (native_source_dir / relative_path).resolve()
        if not source_path.is_relative_to(native_source_dir) or not source_path.is_file():
            raise SetupError(f"locked upstream file is missing: {relative_path}")
        try:
            raw = source_path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise SetupError(f"locked upstream file is unreadable UTF-8: {relative_path}") from error
        canonical_bytes = (
            text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n").encode("utf-8")
        )
        actual_hash = hashlib.sha256(canonical_bytes).hexdigest()
        if actual_hash != expected_hash:
            raise SetupError(
                f"vendored upstream file differs from App-LabRecorder {EXPECTED_UPSTREAM_TAG}: "
                f"{relative_path} (expected {expected_hash}, got {actual_hash})"
            )
        verified[relative_path] = actual_hash
    return {
        "repository": lock.get("repository"),
        "tag": EXPECTED_UPSTREAM_TAG,
        "commit": EXPECTED_UPSTREAM_COMMIT,
        "source_lock_sha256": source_lock_sha256,
        "files": verified,
    }


def native_source_sha256(native_source_dir: Path = NATIVE_SOURCE_DIR) -> str:
    """Fingerprint every native source file so a prebuilt core matches its sources.

    Line endings are normalized so Windows and macOS checkouts agree. Hidden
    files such as Finder's ``.DS_Store`` are ignored.
    """

    native_source_dir = Path(native_source_dir).resolve()
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in native_source_dir.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(native_source_dir).parts)
    )
    for path in files:
        relative = path.relative_to(native_source_dir).as_posix()
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(f"{relative}\0{len(content)}\0".encode("utf-8"))
        digest.update(content)
    return digest.hexdigest()


def probe_core_library(library_path: Path) -> dict[str, Any]:
    """Load the staged C ABI and return its validated, self-described probe."""

    library_path = Path(library_path).resolve()
    if not library_path.is_file():
        raise SetupError(f"native XDF core library is missing: {library_path}")
    dll_directory = None
    try:
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            dll_directory = os.add_dll_directory(str(library_path.parent))
        library = ctypes.CDLL(str(library_path))
        abi_version = library.sr_xdf_core_abi_version
        abi_version.argtypes = []
        abi_version.restype = ctypes.c_uint32
        probe_json = library.sr_xdf_core_probe_json
        probe_json.argtypes = []
        probe_json.restype = ctypes.c_char_p
        exported_abi = int(abi_version())
        raw_probe = probe_json()
    except (AttributeError, OSError) as error:
        raise SetupError(f"native XDF core ABI probe failed: {error}") from error
    finally:
        if dll_directory is not None:
            dll_directory.close()
    if exported_abi != CORE_ABI_VERSION:
        raise SetupError(
            f"native XDF core exports ABI {exported_abi}; ABI {CORE_ABI_VERSION} is required"
        )
    if not raw_probe:
        raise SetupError("native XDF core returned an empty probe")
    try:
        probe = json.loads(raw_probe.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise SetupError("native XDF core returned invalid probe JSON") from error
    if not isinstance(probe, dict) or probe.get("abi_version") != CORE_ABI_VERSION:
        raise SetupError("native XDF core probe does not confirm the required ABI")
    if probe.get("upstream_version") != EXPECTED_UPSTREAM_TAG:
        raise SetupError(
            f"native XDF core does not identify App-LabRecorder {EXPECTED_UPSTREAM_TAG}"
        )
    features = probe.get("features")
    if not isinstance(features, dict):
        raise SetupError("native XDF core probe has no feature map")
    return probe


def canonical_probe_errors(probe: Mapping[str, Any]) -> tuple[str, ...]:
    """Explain why a syntactically valid core is not the canonical backend."""

    errors: list[str] = []
    if probe.get("canonical_xdf") is not True:
        errors.append("probe does not declare canonical_xdf=true")
    if probe.get("byte_order") != "little":
        errors.append("core is not a supported little-endian build")
    features = probe.get("features")
    features = features if isinstance(features, Mapping) else {}
    for feature in REQUIRED_CANONICAL_FEATURES:
        if features.get(feature) is not True:
            errors.append(f"required feature is absent: {feature}")
    return tuple(errors)


def run_synthetic_xdf_smoke(library_path: Path, build_dir: Path) -> dict[str, Any]:
    """Write, merge, and import real XDF fixtures through the installed core."""

    software_path = str(SOFTWARE_ROOT)
    if software_path not in sys.path:
        sys.path.insert(0, software_path)
    try:
        import pyxdf
        from study_runner.data_core.worker.core import NativeXdfCore
    except (ImportError, OSError) as error:
        raise SetupError(f"synthetic XDF smoke dependencies are unavailable: {error}") from error

    smoke_dir = Path(build_dir).resolve() / f"smoke-{uuid.uuid4().hex[:8]}"
    # A normal mkdir avoids Windows TemporaryDirectory ACLs which can deny the
    # native exclusive-create handle even though Python created the directory.
    smoke_dir.mkdir(parents=False, exist_ok=False)
    try:
        core = NativeXdfCore(Path(library_path), require_canonical=True)
        numeric_path = smoke_dir / "numeric.xdf"
        marker_path = smoke_dir / "markers.xdf"
        merged_path = smoke_dir / "merged.xdf"
        _write_smoke_stream(
            core,
            numeric_path,
            stream_id=17,
            name="setup-numeric",
            source_id="study_runner.setup.numeric",
            nominal_rate=10.0,
            channel_format="float32",
            timestamps=(1000.0, 1000.1, 1000.2),
            values=((1.0,), (2.0,), (3.0,)),
        )
        _write_smoke_stream(
            core,
            marker_path,
            stream_id=29,
            name="setup-markers",
            source_id="study_runner.setup.markers",
            nominal_rate=0.0,
            channel_format="string",
            timestamps=(1000.0, 1000.2),
            values=(("start",), ("end",)),
        )
        merge_report = core.merge(
            (("setup_numeric", numeric_path), ("setup_markers", marker_path)),
            merged_path,
            durable=True,
        )
        streams, _header = pyxdf.load_xdf(
            str(merged_path),
            synchronize_clocks=False,
            handle_clock_resets=False,
            dejitter_timestamps=False,
            verbose=False,
        )
        if len(streams) != 2:
            raise SetupError(f"synthetic merged XDF contains {len(streams)} streams; expected two")
        by_source_id = {
            str(stream["info"]["source_id"][0]): stream
            for stream in streams
        }
        numeric = by_source_id.get("study_runner.setup.numeric")
        markers = by_source_id.get("study_runner.setup.markers")
        if numeric is None or markers is None:
            raise SetupError("synthetic merged XDF lost a declared source_id")
        if len(numeric["time_stamps"]) != 3 or len(markers["time_stamps"]) != 2:
            raise SetupError("synthetic merged XDF sample counts differ from their sources")
        if float(numeric["info"]["nominal_srate"][0]) != 10.0:
            raise SetupError("synthetic merged XDF changed the numeric native sample rate")
        clock_times = numeric.get("clock_times")
        if clock_times is None or len(clock_times) != 1:
            raise SetupError("synthetic merged XDF lost its clock-offset chunk")
        rendered_info = json.dumps([stream["info"] for stream in streams], ensure_ascii=False)
        if "study_runner_origin_id" not in rendered_info:
            raise SetupError("synthetic merged XDF lacks deterministic merge provenance")
        return {
            "status": "passed",
            "source_count": int(merge_report["source_count"]),
            "stream_count": len(streams),
            "sample_counts": {
                "study_runner.setup.numeric": 3,
                "study_runner.setup.markers": 2,
            },
        }
    except SetupError:
        raise
    except Exception as error:
        raise SetupError(f"synthetic XDF smoke failed: {error}") from error
    finally:
        shutil.rmtree(smoke_dir)


def _write_smoke_stream(
    core: Any,
    path: Path,
    *,
    stream_id: int,
    name: str,
    source_id: str,
    nominal_rate: float,
    channel_format: str,
    timestamps: Sequence[float],
    values: Sequence[Sequence[Any]],
) -> None:
    header = (
        "<?xml version=\"1.0\"?><info>"
        f"<name>{name}</name><type>SetupSmoke</type><channel_count>1</channel_count>"
        f"<nominal_srate>{nominal_rate}</nominal_srate>"
        f"<channel_format>{channel_format}</channel_format><source_id>{source_id}</source_id>"
        f"<version>1.100000</version><created_at>1000</created_at><uid>{source_id}</uid>"
        "<session_id>setup-smoke</session_id><hostname>localhost</hostname><desc/></info>"
    )
    footer = (
        "<?xml version=\"1.0\"?><info>"
        f"<first_timestamp>{timestamps[0]}</first_timestamp>"
        f"<last_timestamp>{timestamps[-1]}</last_timestamp>"
        f"<sample_count>{len(timestamps)}</sample_count><clock_offsets/></info>"
    )
    writer = core.create_writer(path)
    try:
        writer.write_stream_header(stream_id, header)
        writer.write_samples(
            stream_id,
            timestamps,
            values,
            channel_format=channel_format,
            channel_count=1,
        )
        writer.write_clock_offset(stream_id, timestamps[-1], 0.001)
        writer.boundary()
        writer.write_stream_footer(stream_id, footer)
        writer.close(durable=True)
    finally:
        writer.destroy()


def _run_command(command: Sequence[str], *, quiet: bool) -> str:
    try:
        result = subprocess.run(
            [str(item) for item in command],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as error:
        raise SetupError(f"could not run {command[0]!r}: {error}") from error
    if not quiet and result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        output = result.stdout.strip()
        detail = f":\n{output}" if output else ""
        raise SetupError(f"command failed ({result.returncode}): {' '.join(command)}{detail}")
    return result.stdout


TOOLCHAIN_HELP = (
    "building the XDF core from source needs a C++ compiler. Normal installs download "
    "the prebuilt, tested core and need no compiler. For a source build on macOS, install "
    "Apple's Command Line Tools with 'xcode-select --install' (or Xcode) and retry"
)


def _macos_toolchain(target: Mapping[str, str]) -> dict[str, str]:
    """Select and compile-test the active Apple C++ toolchain (CLT or Xcode)."""

    try:
        compiler = _run_command(
            ["xcrun", "--sdk", "macosx", "--find", "clang++"], quiet=True
        ).strip()
        sdk_path = _run_command(
            ["xcrun", "--sdk", "macosx", "--show-sdk-path"], quiet=True
        ).strip()
    except SetupError as error:
        raise SetupError(f"{TOOLCHAIN_HELP}\n{error}") from error
    if not compiler or not Path(compiler).is_file():
        raise SetupError(f"clang++ is unavailable; {TOOLCHAIN_HELP}")
    if not sdk_path or not Path(sdk_path).is_dir():
        raise SetupError(f"the macOS SDK is unavailable; {TOOLCHAIN_HELP}")
    try:
        developer_dir = _run_command(["xcode-select", "--print-path"], quiet=True).strip()
    except SetupError:
        developer_dir = ""

    with tempfile.TemporaryDirectory(prefix="study-runner-toolchain-") as temporary:
        source = Path(temporary) / "cstdint-preflight.cpp"
        source.write_text(
            "#include <cstdint>\n"
            "int main() { std::uint32_t value = 0; return static_cast<int>(value); }\n",
            encoding="utf-8",
        )
        try:
            _run_command(
                [
                    compiler,
                    "-std=c++20",
                    "-arch",
                    target["cmake_architecture"],
                    "-isysroot",
                    sdk_path,
                    "-fsyntax-only",
                    str(source),
                ],
                quiet=True,
            )
        except SetupError as error:
            raise SetupError(
                "the C++20 toolchain failed its <cstdint> compile preflight. The Command "
                "Line Tools usually no longer match this macOS version; reinstall them "
                f"(or use Xcode) and retry. {TOOLCHAIN_HELP}\n{error}"
            ) from error
    return {
        "developer_dir": developer_dir,
        "compiler": compiler,
        "sdk_path": sdk_path,
    }


def _cmake_cache_value(cache_path: Path, key: str) -> str:
    try:
        lines = cache_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise SetupError(f"CMake cache is unreadable: {cache_path}: {error}") from error
    prefix = f"{key}:"
    for line in lines:
        if line.startswith(prefix) and "=" in line:
            return line.split("=", 1)[1].strip()
    return ""


def _normalized_toolchain_path(value: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(value)))


def _reset_stale_macos_cache(
    build_dir: Path,
    toolchain: Mapping[str, str],
    *,
    resettable_build_dir: Path | None,
) -> bool:
    cache_path = build_dir / "CMakeCache.txt"
    if not cache_path.is_file():
        return False
    cached_compiler = _cmake_cache_value(cache_path, "CMAKE_CXX_COMPILER")
    cached_sdk = _cmake_cache_value(cache_path, "CMAKE_OSX_SYSROOT")
    stale = any(
        cached and _normalized_toolchain_path(cached) != _normalized_toolchain_path(expected)
        for cached, expected in (
            (cached_compiler, toolchain["compiler"]),
            (cached_sdk, toolchain["sdk_path"]),
        )
    )
    if not stale:
        return False
    allowed = (
        resettable_build_dir is not None
        and build_dir.resolve() == Path(resettable_build_dir).expanduser().resolve()
    )
    if not allowed:
        raise SetupError(
            "the selected custom build directory contains a CMake cache for a different "
            "macOS compiler or SDK; choose an empty build directory or remove that generated "
            "cache manually"
        )
    shutil.rmtree(build_dir)
    return True


def _validate_generated_path(path: Path, *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved == REPOSITORY_ROOT or resolved == SOFTWARE_ROOT:
        raise SetupError(f"{label} must not be a repository root")
    if resolved.is_relative_to(REPOSITORY_ROOT) and not resolved.is_relative_to(
        SOFTWARE_ROOT / ".build"
    ):
        raise SetupError(
            f"{label} inside this repository must stay below {SOFTWARE_ROOT / '.build'}"
        )
    return resolved


def _read_build_manifest(stage_dir: Path, target: Mapping[str, str]) -> tuple[dict[str, Any], Path]:
    manifest_path = stage_dir / "worker-build.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SetupError(f"recording core build manifest is unreadable: {manifest_path}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != BUILD_MANIFEST_SCHEMA:
        raise SetupError("recording core build manifest has an unsupported schema")
    if manifest.get("platform_arch") != target["platform_arch"]:
        raise SetupError(
            "recording core build target does not match this host: "
            f"{manifest.get('platform_arch')!r}"
        )
    library_name = manifest.get("core_library")
    if library_name != target["library_name"]:
        raise SetupError("recording core build manifest names an unexpected library")
    library_path = (stage_dir / library_name).resolve()
    if library_path.parent != stage_dir.resolve() or not library_path.is_file():
        raise SetupError("recording core library is missing from its stage directory")
    actual_hash = hashlib.sha256(library_path.read_bytes()).hexdigest()
    if manifest.get("core_sha256") != actual_hash:
        raise SetupError("recording core library hash does not match worker-build.json")
    return manifest, library_path


def probe_stage(stage_dir: Path, target: Mapping[str, str], *, require_canonical: bool) -> dict[str, Any]:
    """Verify a previously staged core without creating any files."""

    stage_dir = Path(stage_dir).expanduser().resolve()
    manifest, library_path = _read_build_manifest(stage_dir, target)
    probe = probe_core_library(library_path)
    errors = canonical_probe_errors(probe)
    if manifest.get("probe") != probe:
        raise SetupError("recording core probe differs from worker-build.json")
    if manifest.get("canonical_xdf") is not (not errors):
        raise SetupError("recording core canonical flag differs from worker-build.json")
    if require_canonical and errors:
        raise SetupError("recording core is not canonical: " + "; ".join(errors))
    recorded_source = manifest.get("native_source_sha256")
    if recorded_source is not None and recorded_source != native_source_sha256():
        raise SetupError(
            "recording core was built from different native sources than this checkout; "
            "rerun the installer to replace it"
        )
    return {
        "ok": True,
        "mode": "probe",
        "platform_arch": target["platform_arch"],
        "stage_dir": str(stage_dir),
        "core_library": str(library_path),
        "canonical_xdf": not errors,
        "canonical_errors": list(errors),
        "probe": probe,
    }


def _replace_stage(source_library: Path, stage_dir: Path, manifest: Mapping[str, Any]) -> None:
    stage_parent = stage_dir.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    temporary = stage_parent / f".{stage_dir.name}.staging-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        shutil.copy2(source_library, temporary / source_library.name)
        (temporary / "worker-build.json").write_text(
            json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if stage_dir.exists():
            if not stage_dir.is_dir():
                raise SetupError(f"stage target is not a directory: {stage_dir}")
            existing_manifest = stage_dir / "worker-build.json"
            if not existing_manifest.is_file():
                raise SetupError(
                    f"refusing to replace an unrecognized directory without worker-build.json: {stage_dir}"
                )
            shutil.rmtree(stage_dir)
        os.replace(temporary, stage_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def build_core(
    *,
    target: Mapping[str, str],
    build_dir: Path,
    stage_dir: Path,
    configuration: str,
    skip_tests: bool,
    require_canonical: bool,
    quiet: bool,
    resettable_build_dir: Path | None = None,
) -> dict[str, Any]:
    """Configure, build, test, install, probe, and stage the native core."""

    if require_canonical and skip_tests:
        raise SetupError("--require-canonical cannot be combined with --skip-tests")
    upstream = verify_upstream_sources()
    if shutil.which("cmake") is None:
        raise SetupError("CMake 3.20 or newer is required to build the recording core")
    macos_toolchain = _macos_toolchain(target) if target["system"] == "macos" else None
    build_dir = _validate_generated_path(build_dir, label="build directory")
    stage_dir = _validate_generated_path(stage_dir, label="stage directory")
    if build_dir == stage_dir or build_dir.is_relative_to(stage_dir) or stage_dir.is_relative_to(build_dir):
        raise SetupError("build and stage directories must not contain each other")
    if macos_toolchain is not None:
        _reset_stale_macos_cache(
            build_dir,
            macos_toolchain,
            resettable_build_dir=resettable_build_dir,
        )
    build_dir.mkdir(parents=True, exist_ok=True)
    install_dir = build_dir / "install"
    if install_dir.exists():
        shutil.rmtree(install_dir)

    configure_command = [
        "cmake",
        "-S",
        str(NATIVE_SOURCE_DIR),
        "-B",
        str(build_dir),
        f"-DCMAKE_BUILD_TYPE={configuration}",
        "-DBUILD_TESTING=ON" if not skip_tests else "-DBUILD_TESTING=OFF",
    ]
    if macos_toolchain is not None:
        configure_command.append(
            f"-DCMAKE_OSX_ARCHITECTURES={target['cmake_architecture']}"
        )
        configure_command.append(f"-DCMAKE_CXX_COMPILER={macos_toolchain['compiler']}")
        configure_command.append(f"-DCMAKE_OSX_SYSROOT={macos_toolchain['sdk_path']}")
    try:
        _run_command(configure_command, quiet=quiet)
        _run_command(
            ["cmake", "--build", str(build_dir), "--config", configuration],
            quiet=quiet,
        )
    except SetupError as error:
        if macos_toolchain is not None and "cstdint" in str(error) and "not found" in str(error):
            raise SetupError(
                "the native build could not find the C++ standard header <cstdint>; the "
                f"active toolchain at {macos_toolchain['developer_dir'] or 'xcrun'} is "
                f"incomplete or damaged. {TOOLCHAIN_HELP}"
            ) from error
        raise
    ctest_status = "skipped"
    if not skip_tests:
        _run_command(
            [
                "ctest",
                "--test-dir",
                str(build_dir),
                "-C",
                configuration,
                "--output-on-failure",
            ],
            quiet=quiet,
        )
        ctest_status = "passed"
    _run_command(
        [
            "cmake",
            "--install",
            str(build_dir),
            "--config",
            configuration,
            "--prefix",
            str(install_dir),
        ],
        quiet=quiet,
    )
    installed = sorted(install_dir.rglob(target["library_name"]), key=lambda path: len(path.parts))
    if len(installed) != 1:
        raise SetupError(
            f"CMake install produced {len(installed)} copies of {target['library_name']}; expected one"
        )
    source_library = installed[0].resolve()
    probe = probe_core_library(source_library)
    canonical_errors = canonical_probe_errors(probe)
    if require_canonical and canonical_errors:
        raise SetupError("recording core is not canonical: " + "; ".join(canonical_errors))
    smoke_details: dict[str, Any] = {"status": "skipped"}
    if not skip_tests:
        smoke_details = run_synthetic_xdf_smoke(source_library, build_dir)
    core_hash = hashlib.sha256(source_library.read_bytes()).hexdigest()
    cmake_version = _run_command(["cmake", "--version"], quiet=True).splitlines()[0]
    manifest = {
        "schema": BUILD_MANIFEST_SCHEMA,
        "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": target["system"],
        "architecture": target["architecture"],
        "platform_arch": target["platform_arch"],
        "configuration": configuration,
        "core_library": target["library_name"],
        "core_sha256": core_hash,
        "abi_version": CORE_ABI_VERSION,
        "canonical_xdf": not canonical_errors,
        "source_lock_sha256": upstream["source_lock_sha256"],
        "native_source_sha256": native_source_sha256(),
        "source": "local_build",
        "probe": probe,
        "tests": {
            "ctest": ctest_status,
            "synthetic_xdf_smoke": smoke_details["status"],
            "synthetic_xdf_smoke_details": smoke_details,
        },
        "toolchain": {
            "cmake": cmake_version,
            **(
                {
                    "developer_dir": macos_toolchain["developer_dir"],
                    "cxx_compiler": macos_toolchain["compiler"],
                    "macos_sdk": macos_toolchain["sdk_path"],
                }
                if macos_toolchain is not None
                else {}
            ),
        },
        "upstream": upstream,
    }
    _replace_stage(source_library, stage_dir, manifest)
    result = probe_stage(stage_dir, target, require_canonical=require_canonical)
    result.update(
        mode="build",
        build_dir=str(build_dir),
        tests=manifest["tests"],
        upstream_verified=True,
    )
    return result


def core_asset_name(platform_arch: str) -> str:
    return f"{CORE_ASSET_PREFIX}{platform_arch}.zip"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_stage(stage_dir: Path, target: Mapping[str, str], output: Path) -> dict[str, Any]:
    """Pack a verified, fully tested stage into a deterministic release asset."""

    result = probe_stage(stage_dir, target, require_canonical=True)
    manifest, library_path = _read_build_manifest(Path(result["stage_dir"]), target)
    tests = manifest.get("tests") or {}
    if tests.get("ctest") != "passed" or tests.get("synthetic_xdf_smoke") != "passed":
        raise SetupError("only a core whose CTest and synthetic XDF smoke passed can be packaged")
    if manifest.get("native_source_sha256") != native_source_sha256():
        raise SetupError("the staged core was not built from this checkout's native sources")
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = library_path.parent / "worker-build.json"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in (library_path, manifest_path):
            info = zipfile.ZipInfo(member.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, member.read_bytes())
    return {
        "ok": True,
        "mode": "package",
        "platform_arch": target["platform_arch"],
        "asset": str(output),
        "asset_sha256": _sha256_file(output),
        "native_source_sha256": manifest["native_source_sha256"],
        "canonical_xdf": True,
        "canonical_errors": [],
        "core_library": str(library_path),
    }


def read_release_info(path: Path = RELEASE_INFO_PATH) -> dict[str, Any] | None:
    """Return the release description shipped inside a source archive, if any."""

    if not path.is_file():
        return None
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SetupError(f"release description is unreadable: {path}: {error}") from error
    if not isinstance(info, dict) or info.get("schema") != RELEASE_INFO_SCHEMA:
        raise SetupError(f"release description has an unsupported schema: {path}")
    return info


def prebuilt_source(
    target: Mapping[str, str], release_info: Mapping[str, Any] | None
) -> dict[str, str]:
    """Describe where the installer downloads this platform's prebuilt core."""

    asset = core_asset_name(target["platform_arch"])
    if release_info is not None:
        repository = str(release_info.get("repository") or "")
        tag = str(release_info.get("tag") or "")
        cores = release_info.get("native_cores")
        if not repository or not tag or not isinstance(cores, Mapping):
            raise SetupError("release description does not name its repository, tag, and cores")
        if target["platform_arch"] not in cores:
            raise SetupError(f"this release has no prebuilt core for {target['platform_arch']}")
        base = f"https://github.com/{repository}/releases/download/{tag}"
    else:
        base = f"https://github.com/{DEFAULT_RELEASE_REPOSITORY}/releases/latest/download"
    return {
        "asset": asset,
        "url": f"{base}/{asset}",
        "checksums_url": f"{base}/SHA256SUMS",
    }


def _expected_asset_sha256(
    asset_name: str,
    platform_arch: str,
    release_info: Mapping[str, Any] | None,
    checksums_path: Path | None,
) -> str:
    """Return the trusted asset hash from the archive's release file or SHA256SUMS."""

    if release_info is not None:
        entry = (release_info.get("native_cores") or {}).get(platform_arch)
        if not isinstance(entry, Mapping) or entry.get("asset") != asset_name:
            raise SetupError(f"release description has no entry for {asset_name}")
        return str(entry.get("sha256") or "").lower()
    if checksums_path is None:
        raise SetupError(
            "this checkout has no release description; pass --checksums with the release's "
            "SHA256SUMS file"
        )
    for line in Path(checksums_path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == asset_name:
            return parts[0].lower()
    raise SetupError(f"SHA256SUMS does not list {asset_name}")


def _strip_quarantine(path: Path) -> None:
    """Remove a browser quarantine flag so macOS does not block loading the core."""

    if sys.platform != "darwin":
        return
    subprocess.run(
        ["xattr", "-d", "com.apple.quarantine", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def install_prebuilt(
    asset_path: Path,
    *,
    target: Mapping[str, str],
    build_dir: Path,
    stage_dir: Path,
    checksums_path: Path | None = None,
    release_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a downloaded core, test it on this machine, then stage it atomically."""

    asset_path = Path(asset_path).expanduser().resolve()
    asset_name = core_asset_name(target["platform_arch"])
    if not asset_path.is_file():
        raise SetupError(f"prebuilt core asset is missing: {asset_path}")
    expected_sha256 = _expected_asset_sha256(
        asset_name, target["platform_arch"], release_info, checksums_path
    )
    actual_sha256 = _sha256_file(asset_path)
    if not expected_sha256 or actual_sha256 != expected_sha256:
        raise SetupError(
            f"prebuilt core checksum mismatch for {asset_name} "
            f"(expected {expected_sha256 or 'a published hash'}, got {actual_sha256})"
        )
    upstream = verify_upstream_sources()
    local_source = native_source_sha256()

    build_dir = _validate_generated_path(build_dir, label="build directory")
    stage_dir = _validate_generated_path(stage_dir, label="stage directory")
    build_dir.mkdir(parents=True, exist_ok=True)
    unpack_dir = build_dir / f"prebuilt-{uuid.uuid4().hex[:8]}"
    unpack_dir.mkdir()
    try:
        allowed = {target["library_name"], "worker-build.json"}
        with zipfile.ZipFile(asset_path) as archive:
            names = {info.filename for info in archive.infolist()}
            if names != allowed:
                raise SetupError(
                    f"prebuilt core asset must contain exactly {sorted(allowed)}, "
                    f"found {sorted(names)}"
                )
            for name in sorted(allowed):
                (unpack_dir / name).write_bytes(archive.read(name))
        manifest, library_path = _read_build_manifest(unpack_dir, target)
        if manifest.get("source_lock_sha256") != upstream["source_lock_sha256"]:
            raise SetupError("prebuilt core is bound to a different upstream source lock")
        if manifest.get("native_source_sha256") != local_source:
            raise SetupError(
                "prebuilt core was built from different native sources than this checkout; "
                "use the matching release or build the core from source"
            )
        tests = manifest.get("tests") or {}
        if tests.get("ctest") != "passed" or tests.get("synthetic_xdf_smoke") != "passed":
            raise SetupError("prebuilt core was published without passing CTest and XDF smoke")
        _strip_quarantine(library_path)
        probe = probe_core_library(library_path)
        errors = canonical_probe_errors(probe)
        if errors:
            raise SetupError("prebuilt core is not canonical: " + "; ".join(errors))
        if manifest.get("probe") != probe:
            raise SetupError("prebuilt core probe differs from its worker-build.json")
        local_smoke = run_synthetic_xdf_smoke(library_path, build_dir)
        manifest = dict(manifest)
        manifest["source"] = "prebuilt"
        manifest["installed_at_utc"] = (
            dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        )
        manifest["asset_sha256"] = actual_sha256
        manifest["tests"] = {**tests, "local_synthetic_xdf_smoke": local_smoke["status"]}
        _replace_stage(library_path, stage_dir, manifest)
    finally:
        shutil.rmtree(unpack_dir, ignore_errors=True)
    _strip_quarantine(stage_dir / target["library_name"])
    result = probe_stage(stage_dir, target, require_canonical=True)
    result.update(mode="install_prebuilt", asset_sha256=actual_sha256)
    return result


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install, verify, or build the canonical XDF core for the Study Runner. "
            "Normal installs use --install-prebuilt; building needs CMake and a C++ compiler."
        )
    )
    parser.add_argument("--build-dir", type=Path, help="CMake working directory")
    parser.add_argument("--stage-dir", type=Path, help="generated core stage directory")
    parser.add_argument(
        "--configuration",
        choices=("Release", "RelWithDebInfo", "Debug"),
        default="Release",
    )
    parser.add_argument("--skip-tests", action="store_true", help="skip CTest and its XDF smoke")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--probe-only", action="store_true", help="verify an existing staged core")
    mode.add_argument(
        "--prebuilt-source",
        action="store_true",
        help="print the prebuilt asset name, its URL, and the SHA256SUMS URL (one per line)",
    )
    mode.add_argument(
        "--install-prebuilt",
        type=Path,
        metavar="ZIP",
        help="verify, locally test, and stage a downloaded prebuilt core",
    )
    mode.add_argument(
        "--package",
        type=Path,
        metavar="ZIP",
        help="pack the staged, fully tested core as a release asset",
    )
    parser.add_argument(
        "--checksums",
        type=Path,
        help="SHA256SUMS of the release (only needed without study-runner-release.json)",
    )
    parser.add_argument("--json", action="store_true", help="print one machine-readable result")
    parser.add_argument(
        "--require-canonical",
        action="store_true",
        help="fail unless every canonical writer and merger feature is present",
    )
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    # This check deliberately happens before any path is created or staged.
    target = supported_target()
    default_build = SOFTWARE_ROOT / ".build" / "xdf_core_build" / target["platform_arch"]
    default_stage = SOFTWARE_ROOT / ".build" / "xdf_core" / target["platform_arch"]
    build_dir = arguments.build_dir or default_build
    stage_dir = arguments.stage_dir or default_stage
    if arguments.probe_only:
        verify_upstream_sources()
        return probe_stage(stage_dir, target, require_canonical=arguments.require_canonical)
    if arguments.prebuilt_source:
        return {"ok": True, "mode": "prebuilt_source", **prebuilt_source(target, read_release_info())}
    if arguments.install_prebuilt is not None:
        return install_prebuilt(
            arguments.install_prebuilt,
            target=target,
            build_dir=build_dir,
            stage_dir=stage_dir,
            checksums_path=arguments.checksums,
            release_info=read_release_info(),
        )
    if arguments.package is not None:
        return package_stage(stage_dir, target, arguments.package)
    return build_core(
        target=target,
        build_dir=build_dir,
        stage_dir=stage_dir,
        configuration=arguments.configuration,
        skip_tests=arguments.skip_tests,
        require_canonical=arguments.require_canonical,
        quiet=arguments.json,
        resettable_build_dir=default_build if arguments.build_dir is None else None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    arguments = parser.parse_args(argv)
    try:
        result = run(arguments)
    except SetupError as error:
        if arguments.json:
            print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        else:
            print(f"Recording core setup failed: {error}", file=sys.stderr)
        return 1
    if arguments.json:
        print(json.dumps(result, sort_keys=True))
    elif result["mode"] == "prebuilt_source":
        print(result["asset"])
        print(result["url"])
        print(result["checksums_url"])
    else:
        status = "canonical" if result["canonical_xdf"] else "non-canonical"
        print(f"Recording core {result['mode']} completed ({status}).")
        print(f"Core: {result['core_library']}")
        if result["canonical_errors"]:
            print("Missing canonical guarantees: " + "; ".join(result["canonical_errors"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
