from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.routes.finalization import bp


class FakeFinalizationService:
    def __init__(self, job):
        self.job = job

    def status(self, *, days=30):
        return {"ok": True, "days": days, "counts": {"completed": 1}, "jobs": [dict(self.job)]}

    def get(self, job_id):
        assert job_id == self.job["job_id"]
        return dict(self.job)


class FinalizationRoutesTests(unittest.TestCase):
    def _app(self, root: Path, job: dict) -> Flask:
        app = Flask(__name__)
        app.config.update(DATA_DIR=root, FINALIZATION_SERVICE=FakeFinalizationService(job))
        app.register_blueprint(bp)
        return app

    def test_job_details_expose_manifest_artifacts_without_rehashing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = "study/participants/p01/sessions/20260731T100000Z__session-1"
            session_root = root / session_path
            session_root.mkdir(parents=True)
            (session_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "artifacts": [
                            {
                                "path": "derived/session.xdf",
                                "role": "merged_xdf",
                                "size_bytes": 42,
                                "sha256": "a" * 64,
                                "local_present": True,
                            },
                            {"path": "../secret", "role": "unsafe"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "job_id": "finalization-1",
                "session_path": session_path,
                "status": "completed",
                "steps": [],
            }
            response = self._app(root, job).test_client().get("/api/finalization/finalization-1")

        self.assertEqual(response.status_code, 200)
        artifacts = response.get_json()["job"]["artifacts"]
        self.assertEqual([item["path"] for item in artifacts], ["derived/session.xdf", "manifest.json"])
        self.assertEqual(artifacts[0]["sha256"], "a" * 64)

    def test_open_folder_uses_job_session_path_not_client_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = "study/participants/p01/sessions/20260731T100000Z__session-1"
            (root / session_path).mkdir(parents=True)
            job = {"job_id": "finalization-1", "session_path": session_path}
            app = self._app(root, job)
            with patch(
                "study_runner.backend.routes.finalization.open_session_folder",
                return_value={"ok": True, "path": str(root / session_path)},
            ) as opener:
                response = app.test_client().post(
                    "/api/finalization/finalization-1/open-folder",
                    json={"session_path": "../ignored"},
                )

        self.assertEqual(response.status_code, 200)
        opener.assert_called_once_with(root, session_path)


if __name__ == "__main__":
    unittest.main()
