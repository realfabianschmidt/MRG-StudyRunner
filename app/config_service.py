import json
from pathlib import Path
from typing import Any


DEFAULT_STIMULUS_CARD: dict[str, Any] = {
    "type": "stimulus",
    "title": "Observe the material",
    "subtitle": "Pay attention to all sensory impressions. The questionnaire will appear automatically.",
    "warmup_duration_ms": 0,
    "duration_ms": 30000,
    "trigger_type": "timer",
    "trigger_content": "",
    "send_signal": True,
    "brainbit_to_lsl": True,
    "brainbit_to_touchdesigner": True,
    "camera_capture_enabled": False,
    "camera_snapshot_interval_ms": 1000,
    "mini_radar_recording_enabled": True,
}


def normalize_config(config_data: dict[str, Any]) -> dict[str, Any]:
    """Migrate old config keys into the current card-based study structure."""
    if "stimulus_duration_ms" in config_data:
        card = dict(DEFAULT_STIMULUS_CARD)
        card["duration_ms"] = config_data.pop("stimulus_duration_ms")
        questions = config_data.get("questions", [])
        if not any(q.get("type") == "stimulus" for q in questions):
            config_data["questions"] = [card] + questions
    config_data.pop("stimulus_duration_ms", None)

    for question_data in config_data.get("questions", []):
        if (
            isinstance(question_data, dict)
            and question_data.get("type") == "choice"
            and question_data.get("multiple") is False
        ):
            question_data["type"] = "single"
            question_data.pop("multiple", None)

        if isinstance(question_data, dict) and question_data.get("type") == "stimulus":
            default_signal = bool(question_data.get("send_signal", True))
            question_data.setdefault("brainbit_to_lsl", default_signal)
            question_data.setdefault("brainbit_to_touchdesigner", default_signal)
            question_data.setdefault("camera_capture_enabled", False)
            question_data.setdefault("camera_snapshot_interval_ms", 1000)
            question_data.setdefault("mini_radar_recording_enabled", default_signal)

    return config_data


def load_config(config_file: Path) -> dict[str, Any]:
    with config_file.open(encoding="utf-8") as file_handle:
        return normalize_config(json.load(file_handle))


def save_config(config_file: Path, config_data: dict[str, Any]) -> None:
    with config_file.open("w", encoding="utf-8") as file_handle:
        json.dump(config_data, file_handle, indent=2, ensure_ascii=False)


def list_studies(studies_dir: Path) -> list[dict[str, Any]]:
    studies_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for file_path in studies_dir.glob("*.json"):
        if not file_path.is_file():
            continue
        results.append({
            "id": file_path.stem,
            "modified": file_path.stat().st_mtime
        })
    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def save_study(studies_dir: Path, config_data: dict[str, Any]) -> None:
    studies_dir.mkdir(parents=True, exist_ok=True)
    study_id = config_data.get("study_id", "Unbenannte Studie").strip()
    safe_id = "".join(c for c in study_id if c.isalnum() or c in " _-") or "unnamed"
    file_path = studies_dir / f"{safe_id}.json"
    save_config(file_path, config_data)


def load_study(studies_dir: Path, study_id: str) -> dict[str, Any]:
    safe_id = "".join(c for c in study_id if c.isalnum() or c in " _-") or "unnamed"
    file_path = studies_dir / f"{safe_id}.json"
    if not file_path.exists():
        raise FileNotFoundError(f"Study {study_id} not found.")
    return load_config(file_path)
