from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.shared.atomic_io import atomic_write_json


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

    def test_concurrent_writers_leave_one_complete_document_and_no_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            barrier = threading.Barrier(8)
            errors: list[Exception] = []

            def write(version: int) -> None:
                try:
                    barrier.wait()
                    atomic_write_json(target, {"version": version, "values": [version] * 100})
                except Exception as error:  # pragma: no cover - assertion reports worker errors
                    errors.append(error)

            threads = [threading.Thread(target=write, args=(version,)) for version in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            payload = json.loads(target.read_text(encoding="utf-8"))
            leftovers = [path for path in Path(tmp).iterdir() if path != target]

        self.assertEqual(errors, [])
        self.assertEqual(payload["values"], [payload["version"]] * 100)
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
