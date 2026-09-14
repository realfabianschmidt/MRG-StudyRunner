"""The repository ships example studies, never a real one.

`study_content/settings/study_config.json` is tracked, and it is also the
file the running app rewrites every time an operator loads or edits a study.
That combination means a lab's own study design lands in `git status` as a
modification to a tracked file, one `git commit -a` away from being published.

It has already happened: a real study replaced the shipped example in this
worktree, and the same folder previously leaked a physical BrainBit MAC
address and serial (see `test_hardware_settings_service.py`, which guards
that half).

So this test pins the shipped active study to one of the two tracked example
presets. Working on a real study is fine -- save it under
`study_content/studies/`, where `.gitignore` keeps every non-example preset
out of the repository, and restore the template before committing:

    git checkout -- software/study_content/settings/study_config.json
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest


SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
STUDY_CONTENT = SOFTWARE_ROOT / "study_content"
ACTIVE_STUDY = STUDY_CONTENT / "settings" / "study_config.json"
EXAMPLE_PRESETS = (
    STUDY_CONTENT / "studies" / "Example Basic Study.study-runner",
    STUDY_CONTENT / "studies" / "Example Sensors Study.study-runner",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ShippedStudyContentTests(unittest.TestCase):
    def test_the_example_presets_are_present(self) -> None:
        """A renamed example would make the guard below vacuously pass."""
        missing = [str(p.relative_to(SOFTWARE_ROOT)) for p in EXAMPLE_PRESETS if not p.is_file()]

        self.assertEqual(
            missing,
            [],
            "the tracked example presets moved or were renamed; update this test "
            "in the same commit: " + ", ".join(missing),
        )

    def test_the_shipped_active_study_is_an_example(self) -> None:
        active = _load(ACTIVE_STUDY)
        examples = [_load(preset) for preset in EXAMPLE_PRESETS]

        # assertTrue, not assertIn: a failing assertIn prints both operands, which
        # would dump the very study this test exists to keep private into the CI log.
        self.assertTrue(
            active in examples,
            "software/study_content/settings/study_config.json holds a study that is "
            f"not a shipped example (study_id {active.get('study_id')!r}). A real study "
            "must not be committed. Save it under software/study_content/studies/ -- "
            "gitignored -- and run: git checkout -- "
            "software/study_content/settings/study_config.json",
        )


if __name__ == "__main__":
    unittest.main()
