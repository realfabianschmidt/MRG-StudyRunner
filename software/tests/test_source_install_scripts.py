"""Static safety and documentation contracts for source install/start scripts."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def text(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


class SourceInstallScriptTests(unittest.TestCase):
    def test_windows_cmd_entrypoints_are_process_local_and_forward_everything(self) -> None:
        for wrapper_name, script_name in (
            ("install-windows.cmd", "install-windows.ps1"),
            ("start-windows.cmd", "start-windows.ps1"),
        ):
            wrapper = text(f"tools/{wrapper_name}")
            self.assertIn(
                '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"',
                wrapper,
            )
            self.assertIn("-NoLogo -NoProfile -ExecutionPolicy Bypass", wrapper)
            self.assertIn(f'-File "%~dp0{script_name}" %*', wrapper)
            self.assertIn("%ERRORLEVEL%", wrapper)
            self.assertNotIn("Set-ExecutionPolicy", wrapper)

    @unittest.skipUnless(os.name == "nt", "Windows command-wrapper behavior")
    def test_windows_cmd_entrypoints_work_in_a_path_with_spaces_and_parentheses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tools_dir = Path(temporary) / "Study Runner (3)" / "tools"
            tools_dir.mkdir(parents=True)
            result_path = tools_dir / "result.txt"
            for wrapper_name, script_name in (
                ("install-windows.cmd", "install-windows.ps1"),
                ("start-windows.cmd", "start-windows.ps1"),
            ):
                shutil.copy2(REPOSITORY_ROOT / "tools" / wrapper_name, tools_dir / wrapper_name)
                (tools_dir / script_name).write_text(
                    "param([string]$ProbeValue)\n"
                    "Set-Content -LiteralPath (Join-Path $PSScriptRoot 'result.txt') "
                    "-Value $ProbeValue\n"
                    "exit 23\n",
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [
                        os.environ.get("COMSPEC", "cmd.exe"),
                        "/d",
                        "/c",
                        f"tools\\{wrapper_name}",
                        "-ProbeValue",
                        "forwarded value",
                    ],
                    cwd=tools_dir.parent,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(completed.returncode, 23, completed.stderr)
                self.assertEqual(result_path.read_text(encoding="utf-8").strip(), "forwarded value")

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
        self.assertIn("[string[]]$ModeArguments", script)
        self.assertIn("$null = Resolve-Python312", script)
        self.assertIn("if (Get-Command cmake -ErrorAction SilentlyContinue)", script)

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
        self.assertIn("[switch]$SelfCheck", windows)
        self.assertIn('$ServerArguments += "--self-check"', windows)
        self.assertIn("--self-check", macos)
        self.assertIn("server_arguments+=(--self-check)", macos)

    def test_installers_never_delete_an_existing_environment_or_user_data(self) -> None:
        for relative_path in ("tools/install-windows.ps1", "tools/install-macos.sh"):
            script = text(relative_path)
            self.assertNotRegex(script, r"(?im)^\s*(?:rm|rmdir|del|Remove-Item)\b")
            self.assertNotIn("saved_results", script)
            self.assertNotIn("study_content", script)

    def test_github_readme_documents_first_install_and_later_start(self) -> None:
        readme = text("README.md")
        for command in (
            ".\\tools\\install-windows.cmd -InstallSystemDependencies",
            ".\\tools\\start-windows.cmd",
            "bash tools/install-macos.sh --install-system-dependencies",
            "bash tools/start-macos.sh",
            "brew install",
        ):
            self.assertIn(command, readme)
        self.assertLess(readme.index("install-windows.cmd"), readme.index("## Project Layout"))
        self.assertRegex(readme, re.compile(r"xcode-select --install", re.IGNORECASE))

    def test_macos_intel_keeps_local_tensorflow_out_of_the_base_install(self) -> None:
        marker = 'sys_platform != "darwin" or platform_machine != "x86_64"'
        for relative_path in (
            "software/requirements.txt",
            "software/study_runner/plugins/sensors/camera_emotion/worker/requirements.txt",
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
        manifest = text("software/study_runner/plugins/sensors/camera_emotion/manifest.json")
        worker = text("software/study_runner/plugins/sensors/camera_emotion/worker/plugin.py")

        self.assertIn("--accept-vgg-face-non-commercial-research-terms", fetcher)
        self.assertIn("EXPECTED_SHA256", fetcher)
        self.assertIn("facial_expression_model_weights.h5", notices)
        self.assertIn("non-commercial research", notices)
        self.assertIn("model_assets/*.h5", ignore)
        self.assertIn("Separately licensed model weights are not downloaded", manifest)
        self.assertNotIn("def _download_model_asset", worker)


if __name__ == "__main__":
    unittest.main()
