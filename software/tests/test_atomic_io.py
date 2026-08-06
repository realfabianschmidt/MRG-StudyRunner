from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.shared.atomic_io import atomic_write_json


class AtomicWriteJsonTests(unittest.TestCase):
    def test_writes_json_and_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "results.json"
            atomic_write_json(target, {"answer": 42})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"answer": 42})

    def test_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "results.json"
            atomic_write_json(target, {"version": 1})
            atomic_write_json(target, {"version": 2})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 2})

    def test_failed_write_keeps_previous_file_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "results.json"
            atomic_write_json(target, {"version": 1})

            with self.assertRaises(TypeError):
                atomic_write_json(target, {"bad": object()})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1})
            leftovers = [path for path in Path(tmp).iterdir() if path.name != "results.json"]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
