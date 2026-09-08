"""DeepFace model/runtime error classification.

Shared by the worker process (server.py) and its supervisor (plugin.py),
which previously kept diverging copies of this logic.
"""
from __future__ import annotations

DEEPFACE_EMOTION_MODEL_NAME = "facial_expression_model_weights.h5"
DEEPFACE_EMOTION_MODEL_URL = (
    "https://github.com/serengil/deepface_models/releases/download/v1.0/"
    "facial_expression_model_weights.h5"
)
DEEPFACE_EMOTION_MODEL_MIN_BYTES = 1_000_000
DEEPFACE_EMOTION_MODEL_SHA256 = (
    "e8e8851d3fa05c001b1c27fd8841dfe08d7f82bb786a53ad8776725b7a1e824c"
)


def classify_model_error(error: str) -> str:
    normalized = str(error or "").lower()
    if "no module named" in normalized or "tf-keras" in normalized or ("tensorflow" in normalized and "requires" in normalized):
        return "missing_package"
    if DEEPFACE_EMOTION_MODEL_NAME.lower() in normalized and "downloading" in normalized:
        return "model_download_failed"
    if DEEPFACE_EMOTION_MODEL_NAME.lower() in normalized:
        return "model_file_missing"
    return "model_warmup_failed"


def suggested_action(error_class: str, asset_path: str) -> str:
    if error_class == "missing_package":
        return "Run the dashboard action 'Repair DeepFace runtime' or run 'pip install -r software/requirements.txt'."
    if error_class in {"model_download_failed", "model_file_missing", "model_file_unreadable"}:
        return (
            "Review THIRD_PARTY_NOTICES.md, then provision the optional model with "
            "'python release_tools/fetch_deepface_model_assets.py "
            "--accept-vgg-face-non-commercial-research-terms', or manually place "
            f"the verified {DEEPFACE_EMOTION_MODEL_NAME} at {asset_path}."
        )
    return "Run the dashboard action 'Repair DeepFace runtime' and restart the Local Emotion Worker."
