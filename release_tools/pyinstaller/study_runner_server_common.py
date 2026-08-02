from __future__ import annotations

import json
from pathlib import Path
from pathlib import PurePosixPath
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


# The BrainBit CLI needs these vendor SDKs, whose native libraries live inside
# the wheels. Without them a packaged build cannot talk to the headset at all.
BRAINBIT_SDK_PACKAGES = ("neurosdk", "em_st_artifacts")


def software_root(spec_path: str) -> Path:
    """Return the Python software folder that holds study_runner and study_content."""
    for candidate in Path(spec_path).resolve().parents:
        root = candidate / "software"
        if (root / "server.py").exists() and (root / "study_runner").exists():
            return root
    raise RuntimeError("Could not locate the repository software/ folder.")


def common_datas(root: Path) -> list[tuple[str, str]]:
    datas = [
        (str(root / "study_runner" / "web"), "study_runner/web"),
        (str(root / "study_content"), "study_content"),
    ]
    model_assets = root / "study_runner" / "integrations" / "camera_emotion" / "worker" / "model_assets"
    model_weights = model_assets / "facial_expression_model_weights.h5"
    if not model_weights.is_file():
        raise RuntimeError(
            "DeepFace model weights are missing: "
            f"{model_weights}. A packaged build without them cannot analyze emotions offline. "
            "Run release_tools/fetch_deepface_model_assets.py first."
        )
    datas.append((str(model_assets), "study_runner/integrations/camera_emotion/worker/model_assets"))
    plugin_manifests = sorted((root / "study_runner" / "integrations").glob("*/manifest.json"))
    if not plugin_manifests:
        raise RuntimeError("No plugin API-v3 manifests were found for the packaged build.")
    for manifest in plugin_manifests:
        datas.append((str(manifest), f"study_runner/integrations/{manifest.parent.name}"))
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Invalid plugin manifest {manifest}: {error}") from error
        ui = payload.get("ui") if isinstance(payload.get("ui"), dict) else {}
        extensions = ui.get("extensions") if isinstance(ui.get("extensions"), dict) else {}
        extra_assets = ui.get("assets") if isinstance(ui.get("assets"), list) else []
        declared_assets = [*extensions.values(), *extra_assets]
        seen_assets: set[str] = set()
        plugin_root = manifest.parent.resolve()
        for raw_asset in declared_assets:
            if not isinstance(raw_asset, str):
                raise RuntimeError(f"Plugin UI asset path must be text in {manifest}")
            relative = PurePosixPath(raw_asset)
            if (
                relative.is_absolute()
                or relative.suffix != ".js"
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise RuntimeError(f"Unsafe plugin UI asset path {raw_asset!r} in {manifest}")
            normalized = relative.as_posix()
            if normalized in seen_assets:
                continue
            seen_assets.add(normalized)
            source = (plugin_root / relative).resolve()
            try:
                source.relative_to(plugin_root)
            except ValueError as error:
                raise RuntimeError(
                    f"Plugin UI asset escapes its plugin directory: {raw_asset!r}"
                ) from error
            if not source.is_file():
                raise RuntimeError(f"Plugin UI asset is missing: {source}")
            destination = PurePosixPath(
                "study_runner", "integrations", manifest.parent.name, *relative.parts[:-1]
            ).as_posix()
            datas.append((str(source), destination))
    # DeepFace's face detector reads cv2's haarcascade XMLs at runtime;
    # PyInstaller's cv2 hook does not reliably collect them.
    datas.extend(collect_data_files("cv2", includes=["**/*.xml"]))
    return datas


def common_binaries(root: Path | None = None) -> list[tuple[str, str]]:
    """Native libraries that PyInstaller's analysis does not find on its own.

    The BrainBit SDKs ctypes-load their library from a fixed path inside their
    own package (neurosdk/libs/win/neurosdk2-x64.dll and the macOS equivalent),
    so the collected destination paths must be preserved exactly.

    Recording-core packaging is intentionally outside this source-workflow
    implementation. The packaged app therefore reports recording
    infrastructure as unavailable until the later signed-bundle work adds a
    verified platform core; it must never bundle an ad-hoc local build.
    """
    strict = sys.platform == "win32" or sys.platform == "darwin"
    binaries: list[tuple[str, str]] = []
    for package in BRAINBIT_SDK_PACKAGES:
        collected = collect_dynamic_libs(package)
        if not collected:
            message = (
                f"No native libraries found for {package}. A packaged build without them "
                "cannot connect to the BrainBit headset. Install the BrainBit requirements "
                "(pyneurosdk2, pyem-st-artifacts) into the build environment first."
            )
            if strict:
                raise RuntimeError(message)
            print(f"WARNING: {message}")
            continue
        binaries.extend(collected)
    return binaries


def common_hidden_imports() -> list[str]:
    return (
        collect_submodules("study_runner.backend")
        + collect_submodules("study_runner.integrations")
        + collect_submodules("neurosdk")
        + collect_submodules("em_st_artifacts")
        + [
            # Launched as "<own executable> --brainbit-cli" in packaged builds,
            # because there is no separate Python interpreter to run the script.
            "study_runner.integrations.brainbit.brainbit_realtime_cli",
            "pythonosc",
            "cv2",
            "deepface",
            "deepface.DeepFace",
            "deepface.models.Demography",
            "deepface.models.demography.Emotion",
            "deepface.models.face_detection.OpenCv",
            "deepface.modules.demography",
            "deepface.modules.detection",
            "deepface.modules.modeling",
            "deepface.modules.normalization",
            "deepface.modules.preprocessing",
            "keras",
            # DeepFace pip-depends on these detector backends and imports
            # them during warmup on macOS ("No module named 'mtcnn'" broke
            # the 0.3.1 release there). They must ship in the bundle.
            "mtcnn",
            "retinaface",
            "tensorflow",
            "study_runner.update_helper",
            "study_runner.update_keys",
            "study_runner.version",
            "tf_keras",
        ]
    )


def common_excludes() -> list[str]:
    # Never exclude mtcnn or retinaface here: DeepFace installs and
    # imports them, and excluding them broke the packaged app on macOS.
    return [
        "IPython",
        "dlib",
        "jupyter",
        "matplotlib",
        "mediapipe",
        "openvino",
        "pytest",
        "tensorboard",
        "torch",
        "torchvision",
    ]
