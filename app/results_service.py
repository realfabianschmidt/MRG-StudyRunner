import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def build_result_filename(study_id: str, now: dt.datetime | None = None) -> str:
    current_time = now or dt.datetime.now()
    timestamp = current_time.strftime(TIMESTAMP_FORMAT)
    safe_study_id = sanitize_study_id_for_filename(study_id)
    return f"{safe_study_id}_{timestamp}.json"


def sanitize_study_id_for_filename(study_id: str) -> str:
    normalized = UNSAFE_FILENAME_CHARS.sub("_", (study_id or "study").strip())
    normalized = normalized.strip("._-")
    if not normalized:
        return "study"
    return normalized[:80]


def save_results_payload(data_dir: Path, study_id: str, result_payload: dict[str, Any]) -> str:
    filename = build_result_filename(study_id)
    file_path = data_dir / filename

    with file_path.open("w", encoding="utf-8") as file_handle:
        json.dump(result_payload, file_handle, indent=2, ensure_ascii=False)

    return filename
