import datetime as dt
import json
from pathlib import Path
from typing import Any


TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


def build_result_filename(study_id: str, now: dt.datetime | None = None) -> str:
    current_time = now or dt.datetime.now()
    timestamp = current_time.strftime(TIMESTAMP_FORMAT)
    safe_study_id = (study_id or "study").strip() or "study"
    return f"{safe_study_id}_{timestamp}.json"


def save_results_payload(data_dir: Path, study_id: str, result_payload: dict[str, Any]) -> str:
    filename = build_result_filename(study_id)
    file_path = data_dir / filename

    with file_path.open("w", encoding="utf-8") as file_handle:
        json.dump(result_payload, file_handle, indent=2, ensure_ascii=False)

    return filename
