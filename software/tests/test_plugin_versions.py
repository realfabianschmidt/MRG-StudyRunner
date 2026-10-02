"""Plugin versions stay citable: a changed plugin must get a new version.

A session records which plugin versions produced its data (meta/manifest.json,
provenance.software). That is only worth citing if a plugin's version changes
whenever its files do. tools/plugin_versions.py keeps a lock of version and
fingerprint per plugin; this test fails the build when they drift apart, with
the exact fix in the message.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOFTWARE_ROOT = PROJECT_ROOT / "software"
for root in (PROJECT_ROOT, SOFTWARE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from tools import plugin_versions as pv  # noqa: E402
from study_runner.contracts.manifest import PluginManifestError, validate_and_normalize_manifest  # noqa: E402

HAS_GIT = shutil.which("git") is not None


class InstalledPluginVersionsTests(unittest.TestCase):
    def test_every_plugin_version_describes_its_files(self) -> None:
        problems = pv.check()
        self.assertEqual(
            problems, [],
            "Plugin versions are out of date - see CONTRIBUTING.md (Plugin versions):\n  - " + "\n  - ".join(problems),
        )

    def test_every_installed_plugin_has_a_semantic_version(self) -> None:
        for key, version in pv.installed_versions().items():
            with self.subTest(plugin=key):
                pv.semver(version)


class ManifestVersionTests(unittest.TestCase):
    def test_a_manifest_version_must_be_major_minor_patch(self) -> None:
        manifest = json.loads((SOFTWARE_ROOT / "tests" / "fixtures" / "packaging_probe" / "manifest.json").read_text(encoding="utf-8"))
        for bad in ("1.0", "v1.0.0", "1.0.0-beta", "01.0.0", "latest"):
            with self.subTest(version=bad), self.assertRaisesRegex(PluginManifestError, "MAJOR.MINOR.PATCH"):
                validate_and_normalize_manifest({**manifest, "version": bad}, directory_name="packaging_probe")


@unittest.skipUnless(HAS_GIT, "git is not available")
class ToolTests(unittest.TestCase):
    """The tool itself, on a throw-away git checkout with one card and one sensor."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.plugins = self.repo / "software" / "study_runner" / "plugins"
        self.lock = self.plugins / "plugin_versions.lock.json"
        self.card = self._plugin("cards", "demo_card", {"capabilities": {"card_contract": {"question_types": ["demo"]}}})
        self.sensor = self._plugin("sensors", "demo_sensor", {"streams": [{"key": "a", "channels": ["x"]}]})
        (self.plugins / "sensors" / "demo_sensor" / "logs").mkdir()
        (self.plugins / "sensors" / "demo_sensor" / "logs" / "run.log").write_text("ignored", encoding="utf-8")
        (self.repo / ".gitignore").write_text("**/logs/\n", encoding="utf-8")
        pv.update(self.lock, self.plugins, self.repo)

    def _plugin(self, category: str, key: str, extra: dict) -> Path:
        folder = self.plugins / category / key
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({"plugin_key": key, "version": "1.0.0", **extra}), encoding="utf-8")
        (folder / "code.js").write_text("export const a = 1;\n", encoding="utf-8")
        return folder

    def _set_version(self, folder: Path, version: str, **changes) -> None:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        (folder / "manifest.json").write_text(json.dumps({**manifest, **changes, "version": version}), encoding="utf-8")

    def _check(self) -> list[str]:
        return pv.check(self.lock, self.plugins, self.repo)

    def _commit(self) -> None:
        # The throw-away fixture repository only; it needs an identity to commit.
        git = ["git", "-C", str(self.repo), "-c", "user.name=Plugin versions test",
               "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True, capture_output=True)

    def test_update_can_run_again_until_the_change_is_committed(self) -> None:
        self._commit()
        (self.card / "code.js").write_text("export const a = 2;\n", encoding="utf-8")
        self._set_version(self.card, "1.1.0")
        pv.update(self.lock, self.plugins, self.repo)
        # Still the same change: no second version step, just record it again.
        (self.card / "code.js").write_text("export const a = 3;\n", encoding="utf-8")
        [problem] = self._check()
        self.assertIn("demo_card: version 1.1.0 is not recorded yet", problem)
        self.assertEqual(pv.update(self.lock, self.plugins, self.repo), ["demo_card 1.0.0 -> 1.1.0"])
        self.assertEqual(self._check(), [])
        # Once committed, the next change needs its own version again.
        self._commit()
        (self.card / "code.js").write_text("export const a = 4;\n", encoding="utf-8")
        self.assertIn("Raise the version", self._check()[0])
        with self.assertRaises(pv.PluginVersionError):
            pv.update(self.lock, self.plugins, self.repo)

    def test_an_unchanged_checkout_passes_and_ignored_files_do_not_count(self) -> None:
        self.assertEqual(self._check(), [])
        (self.plugins / "sensors" / "demo_sensor" / "logs" / "run.log").write_text("more", encoding="utf-8")
        self.assertEqual(self._check(), [])

    def test_a_changed_plugin_without_a_new_version_fails_with_the_fix(self) -> None:
        (self.card / "code.js").write_text("export const a = 2;\n", encoding="utf-8")
        [problem] = self._check()
        self.assertIn("demo_card: its files changed since version 1.0.0", problem)
        self.assertIn("Raise the version", problem)
        with self.assertRaises(pv.PluginVersionError):
            pv.update(self.lock, self.plugins, self.repo)

    def test_a_raised_version_is_recorded_by_update(self) -> None:
        (self.card / "code.js").write_text("export const a = 2;\n", encoding="utf-8")
        self._set_version(self.card, "1.0.1")
        self.assertIn("is not recorded yet", self._check()[0])
        self.assertEqual(pv.update(self.lock, self.plugins, self.repo), ["demo_card 1.0.0 -> 1.0.1"])
        self.assertEqual(self._check(), [])

    def test_a_changed_data_contract_needs_a_major_step(self) -> None:
        self._set_version(self.sensor, "1.1.0", streams=[{"key": "a", "channels": ["x", "y"]}])
        [problem] = self._check()
        self.assertIn("data contract", problem)
        self.assertIn("MAJOR", problem)
        with self.assertRaises(pv.PluginVersionError):
            pv.update(self.lock, self.plugins, self.repo)
        self._set_version(self.sensor, "2.0.0")
        pv.update(self.lock, self.plugins, self.repo)
        self.assertEqual(self._check(), [])

    def test_a_version_may_not_go_down(self) -> None:
        self._set_version(self.card, "0.9.0")
        self.assertIn("version went down", self._check()[0])

    def test_new_and_removed_plugins_must_be_recorded(self) -> None:
        self._plugin("outputs", "demo_output", {})
        shutil.rmtree(self.card)
        problems = self._check()
        self.assertTrue(any("demo_output: new plugin" in p for p in problems))
        self.assertTrue(any("demo_card: in the lock but no longer installed" in p for p in problems))

    def test_windows_and_unix_line_endings_give_the_same_fingerprint(self) -> None:
        code = self.card / "code.js"
        code.write_bytes(b"export const a = 1;\nexport const b = 2;\n")
        unix = pv.content_sha256(self.card, pv.plugin_files(self.card, self.repo))
        code.write_bytes(b"export const a = 1;\r\nexport const b = 2;\r\n")
        self.assertEqual(pv.content_sha256(self.card, pv.plugin_files(self.card, self.repo)), unix)
        code.write_bytes(b"export const a = 1;\nexport const b = 3;\n")
        self.assertNotEqual(pv.content_sha256(self.card, pv.plugin_files(self.card, self.repo)), unix)

    def test_outside_a_git_checkout_only_versions_are_compared(self) -> None:
        with tempfile.TemporaryDirectory() as extracted:
            copy = Path(extracted) / "release"
            shutil.copytree(self.repo, copy, ignore=shutil.ignore_patterns(".git"))
            copied_plugins = copy / "software" / "study_runner" / "plugins"
            (copied_plugins / "cards" / "demo_card" / "code.js").unlink()  # e.g. an export-ignored file
            self.assertEqual(pv.check(copied_plugins / "plugin_versions.lock.json", copied_plugins, copy), [])


if __name__ == "__main__":
    unittest.main()
