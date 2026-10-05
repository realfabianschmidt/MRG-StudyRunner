from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from study_runner_admin.app import delete_participant, find_participant, generate_participant_id_for_study, list_participants


def test_generate_participant_id_has_prefix() -> None:
    participant_id = generate_participant_id_for_study(prefix="P")
    assert participant_id.startswith("P-")


def test_participant_listing_and_lookup_in_temp_data_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        study_dir = data_dir / "saved_results" / "DemoStudy" / "participants" / "P-0001" / "sessions"
        study_dir.mkdir(parents=True, exist_ok=True)
        (study_dir / "20260811T163356Z__session-1").mkdir(parents=True, exist_ok=True)
        (study_dir / "20260811T163356Z__session-1" / "answers.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

        items = list_participants("DemoStudy", data_dir=data_dir)
        assert len(items) == 1
        assert items[0]["participant_id"] == "P-0001"

        result = find_participant("P-0001", data_dir=data_dir)
        assert result["study_count"] == 1
        assert result["matches"][0]["sessions"][0]["session_folder"].startswith("20260811T163356Z")


def test_delete_participant_removes_folder_and_creates_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        participant_dir = data_dir / "saved_results" / "DemoStudy" / "participants" / "P-0001"
        participant_dir.mkdir(parents=True, exist_ok=True)
        session_dir = participant_dir / "sessions" / "20260811T163356Z__session-1"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "answer.json").write_text("{}", encoding="utf-8")

        result = delete_participant(
            "P-0001",
            study_id="DemoStudy",
            data_dir=data_dir,
            reason="test deletion",
            archive_first=True,
        )

        assert result["status"] == "deleted"
        assert not participant_dir.exists()
        assert result["archive_path"] is not None
        assert Path(result["archive_path"]).exists()


def test_delete_participant_without_archive_keeps_tree_clean() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        participant_dir = data_dir / "saved_results" / "DemoStudy" / "participants" / "P-0001"
        participant_dir.mkdir(parents=True, exist_ok=True)
        (participant_dir / "sessions").mkdir(parents=True, exist_ok=True)

        result = delete_participant("P-0001", study_id="DemoStudy", data_dir=data_dir, archive_first=False)

        assert result["status"] == "deleted"
        assert not participant_dir.exists()
        assert result["archive_path"] is None


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main(["-q", __file__]))
