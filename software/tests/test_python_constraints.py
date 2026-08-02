"""Contracts for the bounded CPython 3.12 dependency constraints."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS_ROOT = REPOSITORY_ROOT / "software" / "constraints"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")
REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)")


def normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def active_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def pins(filename: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in active_lines(CONSTRAINTS_ROOT / filename):
        match = PIN.fullmatch(line)
        if match is None:
            raise AssertionError(f"constraint is not an exact pin: {line}")
        name, version = match.groups()
        result[normalized_name(name)] = version
    return result


def requirement_names(relative_path: str) -> set[str]:
    names: set[str] = set()
    for line in active_lines(REPOSITORY_ROOT / relative_path):
        match = REQUIREMENT_NAME.match(line)
        if match:
            names.add(normalized_name(match.group(1)))
    return names


class PythonConstraintTests(unittest.TestCase):
    def test_recording_science_versions_remain_exact(self) -> None:
        common = pins("py312-common.txt")
        self.assertEqual(common["numpy"], "1.26.4")
        self.assertEqual(common["pylsl"], "1.18.2")
        self.assertEqual(common["pyxdf"], "1.16.8")

    def test_every_direct_requirement_has_a_compatibility_pin(self) -> None:
        constrained = pins("py312-common.txt") | pins("py312-local-emotion.txt")
        for relative_path in (
            "software/requirements.txt",
            "software/study_runner/integrations/camera_emotion/worker/requirements.txt",
        ):
            missing = requirement_names(relative_path) - constrained.keys()
            self.assertEqual(missing, set(), f"unconstrained direct requirements: {missing}")

    def test_emotion_stack_is_separate_from_cross_platform_common_set(self) -> None:
        common = pins("py312-common.txt")
        local_emotion = pins("py312-local-emotion.txt")
        for package in ("deepface", "tf-keras", "tensorflow", "keras"):
            self.assertIn(package, local_emotion)
            self.assertNotIn(package, common)
        self.assertEqual(pins("py312-bootstrap.txt"), {"pip": "25.3"})

    def test_only_one_opencv_distribution_is_selected(self) -> None:
        self.assertEqual(pins("py312-common.txt")["opencv-python"], "4.11.0.86")
        for relative_path in (
            "software/requirements.txt",
            "software/study_runner/integrations/camera_emotion/worker/requirements.txt",
            "software/constraints/py312-common.txt",
            "software/constraints/py312-local-emotion.txt",
        ):
            content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("opencv-python-headless", content, relative_path)

    def test_installers_ci_and_runtime_repair_consume_constraints(self) -> None:
        paths = (
            "tools/install-windows.ps1",
            "tools/install-macos.sh",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            "software/study_runner/integrations/camera_emotion/worker/plugin.py",
        )
        for relative_path in paths:
            content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("py312-common.txt", content, relative_path)
            self.assertIn("py312-local-emotion.txt", content, relative_path)

        for relative_path in (
            "tools/install-windows.ps1",
            "tools/install-macos.sh",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
        ):
            content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("py312-bootstrap.txt", content, relative_path)

    def test_constraints_are_exact_but_do_not_claim_hash_locking(self) -> None:
        for path in CONSTRAINTS_ROOT.glob("py312-*.txt"):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("--hash=", content)
            self.assertIn("not a", content.casefold())

        documentation = (REPOSITORY_ROOT / "docs" / "release-and-update.md").read_text(
            encoding="utf-8"
        ).casefold()
        self.assertIn("not a hash-locked", documentation)
        self.assertIn("transitive", documentation)


if __name__ == "__main__":
    unittest.main()
