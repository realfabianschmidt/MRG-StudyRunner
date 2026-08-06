"""Static safety and documentation contracts for source install/start scripts."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def text(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


class SourceInstallScriptTests(unittest.TestCase):
    def test_windows_installer_has_explicit_system_packages_and_recording_gate(self) -> None:
        script = text("tools/install-windows.ps1")
        for required in (
            "Python.Python.3.12",
            "Kitware.CMake",
            "Microsoft.VisualStudio.2022.BuildTools",
            "Microsoft.VisualStudio.Workload.VCTools",
            "software\\requirements.txt",
            "py312-bootstrap.txt",
            "py312-common.txt",
            "py312-local-emotion.txt",
            '".venv"',
            "setup_recording_worker.py",
            "--probe-only --require-canonical",
            "--require-canonical",
        ):
            self.assertIn(required, script)
        self.assertIn("[switch]$InstallSystemDependencies", script)
        self.assertIn("[switch]$SkipRecordingCore", script)

    def test_macos_installer_has_official_toolchain_and_recording_gate(self) -> None:
        script = text("tools/install-macos.sh")
        for required in (
            "xcode-select --install",
            "python@3.12 cmake",
            'venv_path="$repository_root/.venv"',
            "software/requirements.txt",
            "py312-bootstrap.txt",
            "py312-common.txt",
            "py312-local-emotion.txt",
            "setup_recording_worker.py",
            "--probe-only --require-canonical",
            "--require-canonical",
        ):
            self.assertIn(required, script)
        self.assertIn("--install-system-dependencies", script)
        self.assertIn("--skip-recording-core", script)
        self.assertIn('if [[ "$host_arch" == "arm64" ]]', script)
        self.assertNotRegex(
            script,
            r'dependency_constraints=.*py312-local-emotion',
        )

    def test_daily_start_scripts_do_not_install_or_mutate_dependencies(self) -> None:
        windows = text("tools/start-windows.ps1")
        macos = text("tools/start-macos.sh")
        for script in (windows, macos):
            self.assertIn("server.py", script)
            self.assertIn(".venv", script)
            self.assertNotIn("pip install", script)
            self.assertNotIn("setup_recording_worker", script)
            self.assertNotRegex(script, r"(?im)^\s*(?:rm|rmdir|del|Remove-Item)\b")

    def test_installers_never_delete_an_existing_environment_or_user_data(self) -> None:
        for relative_path in ("tools/install-windows.ps1", "tools/install-macos.sh"):
            script = text(relative_path)
            self.assertNotRegex(script, r"(?im)^\s*(?:rm|rmdir|del|Remove-Item)\b")
            self.assertNotIn("saved_results", script)
            self.assertNotIn("study_content", script)

    def test_github_readme_documents_first_install_and_later_start(self) -> None:
        readme = text("README.md")
        for command in (
            ".\\tools\\install-windows.ps1 -InstallSystemDependencies",
            ".\\tools\\start-windows.ps1",
            "bash tools/install-macos.sh --install-system-dependencies",
            "bash tools/start-macos.sh",
            "brew install",
        ):
            self.assertIn(command, readme)
        self.assertRegex(readme, re.compile(r"xcode-select --install", re.IGNORECASE))

    def test_macos_intel_keeps_local_tensorflow_out_of_the_base_install(self) -> None:
        marker = 'sys_platform != "darwin" or platform_machine != "x86_64"'
        for relative_path in (
            "software/requirements.txt",
            "software/study_runner/plugins/camera_emotion/worker/requirements.txt",
        ):
            requirements = text(relative_path)
            self.assertRegex(requirements, rf"(?m)^deepface[^\n]+; {re.escape(marker)}$")
            self.assertRegex(requirements, rf"(?m)^tf-keras[^\n]+; {re.escape(marker)}$")

        for relative_path in (
            "README.md",
            "docs/start-here.de.md",
            "docs/operator-guide.md",
        ):
            documentation = text(relative_path).casefold()
            self.assertIn("macos intel", documentation)
            self.assertIn("remote_worker", documentation)

    def test_separately_licensed_model_is_not_a_silent_release_dependency(self) -> None:
        fetcher = text("release_tools/fetch_deepface_model_assets.py")
        notices = text("THIRD_PARTY_NOTICES.md")
        ignore = text(".gitignore")
        manifest = text("software/study_runner/plugins/camera_emotion/manifest.json")
        worker = text("software/study_runner/plugins/camera_emotion/worker/plugin.py")

        self.assertIn("--accept-vgg-face-non-commercial-research-terms", fetcher)
        self.assertIn("EXPECTED_SHA256", fetcher)
        self.assertIn("facial_expression_model_weights.h5", notices)
        self.assertIn("non-commercial research", notices)
        self.assertIn("model_assets/*.h5", ignore)
        self.assertIn("Separately licensed model weights are not downloaded", manifest)
        self.assertNotIn("def _download_model_asset", worker)


if __name__ == "__main__":
    unittest.main()
