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
            'PYTHON_VERSION="3.12.10"',
            'python-${PYTHON_VERSION}-macos11.pkg',
            "https://www.python.org/ftp/python/",
            "8373e58da4ea146b3eb1c1f9834f19a319440b6b679b06050b1f9ee3237aa8e4",
            "Developer ID Installer: Python Software Foundation (BMM5U3QVKW)",
            "shasum -a 256",
            "pkgutil --check-signature",
            "sudo /usr/sbin/installer",
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
            "sysctl.proc_translated",
            "MACOS_MINIMUM_MAJOR=15",
            "MACOS_MINIMUM_MINOR=6",
            "XCODE_REQUIRED_MAJOR=26",
            'XCODE_REFERENCE_VERSION="26.3"',
            "/Applications/Xcode-26.3.app/Contents/Developer",
            "https://developer.apple.com/download/all/?q=Xcode%2026.3",
            "/Applications/Xcode.app/Contents/Developer",
            'export DEVELOPER_DIR="$developer_dir"',
            "xcodebuild -version",
            "xcrun --sdk macosx --find clang++",
            "xcrun --sdk macosx --show-sdk-path",
            "#include <cstdint>",
            "-std=c++20",
            'venv_path="$repository_root/.venv"',
            "software/requirements.txt",
            "py312-bootstrap.txt",
            "py312-common.txt",
            "py312-local-emotion.txt",
            "py312-build-tools.txt",
            'export PATH="$venv_path/bin:$PATH"',
            '"$venv_python" -m pip install --constraint "$build_tools_constraints" cmake',
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
        self.assertNotIn("xcode-select --install", script)
        self.assertNotIn("xcode-select --switch", script)
        self.assertNotIn("sudo xcode-select", script)
        self.assertNotRegex(script, r"(?m)^\s*open\s+")
        self.assertNotIn("xip --expand", script)
        self.assertNotRegex(script, re.compile(r"\bhomebrew\b|\bbrew\b", re.IGNORECASE))

        core_setup = text("tools/setup_recording_worker.py")
        for required in (
            "XCODE_REQUIRED_MAJOR = 26",
            'XCODE_REFERENCE_VERSION = "26.3"',
            "/Applications/Xcode-26.3.app/Contents/Developer",
            "https://developer.apple.com/download/all/?q=Xcode%2026.3",
            "_full_xcode_developer_dir",
            "_macos_toolchain",
            'os.environ["DEVELOPER_DIR"]',
            "#include <cstdint>",
            'f"-DCMAKE_CXX_COMPILER=',
            'f"-DCMAKE_OSX_SYSROOT=',
            "_reset_stale_macos_cache",
        ):
            self.assertIn(required, core_setup)
        self.assertNotIn("xcode-select --switch", core_setup)
        self.assertNotIn("sudo xcode-select", core_setup)
        for content in (script, core_setup):
            self.assertNotRegex(content, r"(?m)^\s*(?:mas|xcodes)\s+install\b")

        build_tools = text("software/constraints/py312-build-tools.txt")
        self.assertRegex(build_tools, r"(?m)^cmake==3\.31\.10$")

        release_workflow = text(".github/workflows/release.yml")
        for required in (
            "bash -n tools/install-macos.sh",
            "3.12|$(uname -m)",
            ".venv/bin/cmake --version",
            "py312-build-tools.txt",
            "test -x .venv/bin/ctest",
        ):
            self.assertIn(required, release_workflow)

    def test_macos_installer_enforces_the_15_6_version_tuple(self) -> None:
        script = text("tools/install-macos.sh")
        major_match = re.search(r"MACOS_MINIMUM_MAJOR=(\d+)", script)
        minor_match = re.search(r"MACOS_MINIMUM_MINOR=(\d+)", script)
        self.assertIsNotNone(major_match)
        self.assertIsNotNone(minor_match)
        minimum = (int(major_match.group(1)), int(minor_match.group(1)))

        self.assertLess((15, 5), minimum)
        self.assertGreaterEqual((15, 6), minimum)
        self.assertGreaterEqual((16, 0), minimum)
        self.assertIn(
            "macos_major == MACOS_MINIMUM_MAJOR && macos_minor < MACOS_MINIMUM_MINOR",
            script,
        )
        self.assertIn("if ((skip_recording_core == 0)); then", script)
        self.assertNotIn("xcode-select --install", script)

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
        ):
            self.assertIn(command, readme)
        for instruction in (
            "study-runner-source.zip",
            "study-runner-source.tar.gz",
            "#### First installation",
            "#### Every later start",
            "#### Create the macOS desktop shortcut",
            "Study Runner.command",
            "drag the Study Runner folder",
            "Keep the terminal window open",
        ):
            self.assertIn(instruction, readme)
        self.assertLess(readme.index("install-windows.cmd"), readme.index("## Project Layout"))
        self.assertLess(readme.index("study-runner-source.zip"), readme.index("## Project Layout"))
        self.assertIn("macOS 15.6 or newer", readme)
        self.assertIn("Xcode 26.3 Universal", readme)
        self.assertIn("standalone Command Line Tools", readme)
        for required in (
            'open "https://developer.apple.com/download/all/?q=Xcode%2026.3"',
            'xip --expand "$HOME/Downloads/Xcode_26.3.xip"',
            "if [[ -e /Applications/Xcode-26.3.app ]]",
            "sudo mv Xcode.app /Applications/Xcode-26.3.app",
            'sudo env DEVELOPER_DIR="$DEVELOPER_DIR" /usr/bin/xcodebuild -runFirstLaunch',
            "/usr/bin/xcrun --sdk macosx --find clang++",
            "/usr/bin/xcrun --sdk macosx --show-sdk-path",
        ):
            self.assertIn(required, readme)
        self.assertRegex(readme, r"Do not use\s+`xcode-select --install`")
        self.assertNotIn("brew install", readme.casefold())
        self.assertNotIn("brew.sh", readme.casefold())
        self.assertNotRegex(readme, r"(?m)^\s*(?:mas|xcodes)\s+install\b")

        german = text("docs/start-here.de.md")
        for instruction in (
            "study-runner-source.zip",
            "study-runner-source.tar.gz",
            "Desktop-Verknuepfung erstellen",
            "Study Runner.command",
            "Ctrl+C",
            "Xcode 26.3 Universal",
            'open "https://developer.apple.com/download/all/?q=Xcode%2026.3"',
            'xip --expand "$HOME/Downloads/Xcode_26.3.xip"',
            "sudo mv Xcode.app /Applications/Xcode-26.3.app",
            'sudo env DEVELOPER_DIR="$DEVELOPER_DIR" /usr/bin/xcodebuild -runFirstLaunch',
        ):
            self.assertIn(instruction, german)
        self.assertNotRegex(german, r"(?m)^\s*(?:mas|xcodes)\s+install\b")

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
