from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


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
            "Run release_tools/fetch-deepface-model-assets.py first."
        )
    datas.append((str(model_assets), "study_runner/integrations/local_emotion_worker/model_assets"))
    return datas


def common_hidden_imports() -> list[str]:
    return (
        collect_submodules("study_runner.backend")
        + collect_submodules("study_runner.integrations")
        + [
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
            "tensorflow",
            "study_runner.update_helper",
            "study_runner.update_keys",
            "study_runner.version",
            "tf_keras",
        ]
    )


def common_excludes() -> list[str]:
    return [
        "IPython",
        "dlib",
        "jupyter",
        "matplotlib",
        "mediapipe",
        "mtcnn",
        "openvino",
        "pytest",
        "retinaface",
        "tensorboard",
        "torch",
        "torchvision",
    ]
