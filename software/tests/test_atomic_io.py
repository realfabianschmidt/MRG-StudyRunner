from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.shared import atomic_io
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

    def test_a_briefly_held_target_is_replaced_after_a_retry(self) -> None:
        # Windows: a scanner or reader holding the target makes replace fail for milliseconds.
        real_replace = atomic_io.os.replace
        calls = []

        def held_twice(source, target):
            calls.append(target)
            if len(calls) <= 2:
                raise PermissionError(5, "Access is denied")
            return real_replace(source, target)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "study_config.json"
            atomic_write_json(target, {"version": 1})
            with patch.object(atomic_io.os, "replace", side_effect=held_twice), patch.object(atomic_io, "REPLACE_RETRY_SECONDS", 0.0):
                atomic_write_json(target, {"version": 2})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 2})
            self.assertEqual(len(calls), 3)

    def test_a_permanently_held_target_still_fails_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "study_config.json"
            atomic_write_json(target, {"version": 1})
            with (
                patch.object(atomic_io.os, "replace", side_effect=PermissionError(5, "Access is denied")),
                patch.object(atomic_io, "REPLACE_RETRY_SECONDS", 0.0),
                self.assertRaises(PermissionError),
            ):
                atomic_write_json(target, {"version": 2})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1})
            self.assertEqual([path.name for path in Path(tmp).iterdir()], ["study_config.json"])

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
