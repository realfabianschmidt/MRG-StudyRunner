from __future__ import annotations

from pathlib import Path
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
    model_assets = root / "study_runner" / "integrations" / "local_emotion_worker" / "model_assets"
    model_weights = model_assets / "facial_expression_model_weights.h5"
    if not model_weights.is_file():
        raise RuntimeError(
            "DeepFace model weights are missing: "
            f"{model_weights}. A packaged build without them cannot analyze emotions offline. "
            "Run release_tools/fetch_deepface_model_assets.py first."
        )
    datas.append((str(model_assets), "study_runner/integrations/local_emotion_worker/model_assets"))
    # DeepFace's face detector reads cv2's haarcascade XMLs at runtime;
    # PyInstaller's cv2 hook does not reliably collect them.
    datas.extend(collect_data_files("cv2", includes=["**/*.xml"]))
    return datas


def common_binaries() -> list[tuple[str, str]]:
    """Native libraries that PyInstaller's analysis does not find on its own.

    The BrainBit SDKs ctypes-load their library from a fixed path inside their
    own package (neurosdk/libs/win/neurosdk2-x64.dll and the macOS equivalent),
    so the collected destination paths must be preserved exactly.

    Linux is deliberately not strict: neurosdk loads a bare "libneurosdk2.so"
    there, i.e. it expects a system-wide install rather than a bundled copy.
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
