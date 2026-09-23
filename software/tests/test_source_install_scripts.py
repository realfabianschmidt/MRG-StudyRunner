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

    def test_uv_bootstrap_pins_one_checked_download_per_platform(self) -> None:
        pins = dict(
            line.split("=", 1)
            for line in text("software/constraints/uv-bootstrap.txt").splitlines()
            if line.strip() and not line.startswith("#")
        )
        self.assertRegex(pins["uv_version"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(pins["python_version"], r"^3\.12\.\d+$")
        expected_assets = {
            "macos-x64": "uv-x86_64-apple-darwin.tar.gz",
            "macos-arm64": "uv-aarch64-apple-darwin.tar.gz",
            "windows-x64": "uv-x86_64-pc-windows-msvc.zip",
        }
        for platform_arch, asset in expected_assets.items():
            name, sha256 = pins[platform_arch].split()
            self.assertEqual(name, asset)
            self.assertRegex(sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(set(pins), {"uv_version", "python_version", *expected_assets})

    def test_windows_installer_is_project_local_and_needs_no_system_packages(self) -> None:
        script = text("tools/install-windows.ps1")
        for required in (
            "software\\constraints\\uv-bootstrap.txt",
            "https://github.com/astral-sh/uv/releases/download/$UvVersion/$UvAsset",
            "Get-FileHash -LiteralPath $UvArchive -Algorithm SHA256",
            '$env:UV_CACHE_DIR = Join-Path $ToolsPath "uv-cache"',
            '$env:UV_PYTHON_INSTALL_DIR = Join-Path $ToolsPath "python"',
            '$env:UV_NO_CONFIG = "1"',
            "python install $PythonVersion --no-bin --no-registry",
            "venv --seed --managed-python --python $PythonVersion $VenvPath",
            "Push-Location -LiteralPath $RepositoryRoot",
            'software\\constraints\\py312-bootstrap.txt"',
            'software\\constraints\\py312-common.txt"',
            'software\\constraints\\py312-local-emotion.txt"',
            "software\\requirements.txt",
            '".venv"',
            "setup_recording_worker.py",
            "--probe-only --require-canonical",
            "--prebuilt-source",
            "--install-prebuilt $CoreArchive",
            "STUDY_RUNNER_CORE_ASSET_DIR",
        ):
            self.assertIn(required, script)
        self.assertIn("[switch]$InstallSystemDependencies", script)
        self.assertIn("[switch]$SkipRecordingCore", script)
        self.assertIn("[switch]$BuildCoreFromSource", script)
        # uv splits --constraint values at spaces: never pass an absolute path.
        self.assertNotRegex(script, r"--constraint \$\w+Path\b")
        for forbidden in ("winget", "VisualStudio", "RunAs", "Set-ExecutionPolicy", "HKLM:"):
            self.assertNotIn(forbidden, script)

    def test_macos_installer_is_project_local_and_needs_no_admin_or_compiler(self) -> None:
        script = text("tools/install-macos.sh")
        for required in (
            'uv_bootstrap="$repository_root/software/constraints/uv-bootstrap.txt"',
            'https://github.com/astral-sh/uv/releases/download/$uv_version/$uv_asset',
            "shasum -a 256",
            "--proto '=https' --tlsv1.2",
            'export UV_CACHE_DIR="$tools_path/uv-cache"',
            'export UV_PYTHON_INSTALL_DIR="$tools_path/python"',
            "export UV_NO_CONFIG=1",
            'python install "$python_version" --no-bin',
            'venv --seed --managed-python --python "$python_version" "$venv_path"',
            'cd "$repository_root"',
            '"$relative_constraints/py312-bootstrap.txt"',
            '"$relative_constraints/py312-common.txt"',
            '"$relative_constraints/py312-local-emotion.txt"',
            'venv_path="$repository_root/.venv"',
            "software/requirements.txt",
            "setup_recording_worker.py",
            "--probe-only --require-canonical",
            "--prebuilt-source",
            "--install-prebuilt",
            "STUDY_RUNNER_CORE_ASSET_DIR",
            "sysctl.proc_translated",
        ):
            self.assertIn(required, script)
        self.assertIn("--skip-recording-core", script)
        self.assertIn("--build-core-from-source", script)
        self.assertIn("--install-system-dependencies", script)
        self.assertIn('if [[ "$host_arch" == "arm64" ]]', script)
        self.assertNotRegex(script, r'dependency_constraints=.*py312-local-emotion')
        # uv splits --constraint values at spaces: never pass an absolute path.
        self.assertNotRegex(script, r'--constraint "\$(?!relative_constraints)')
        self.assertNotRegex(script, r"(?m)^[^#]*\bsudo\b")
        for forbidden in ("xcodebuild", "xcrun", "/usr/sbin/installer", "xip --expand", "pkgutil"):
            self.assertNotIn(forbidden, script)
        self.assertNotRegex(script, r"(?m)^\s*open\s+")
        self.assertNotRegex(script, re.compile(r"\bhomebrew\b|\bbrew\b", re.IGNORECASE))

    def test_core_setup_accepts_any_apple_toolchain_for_source_builds(self) -> None:
        core_setup = text("tools/setup_recording_worker.py")
        for required in (
            "_macos_toolchain",
            'f"-DCMAKE_CXX_COMPILER=',
            'f"-DCMAKE_OSX_SYSROOT=',
            "_reset_stale_macos_cache",
            "#include <cstdint>",
            "xcode-select --install",
        ):
            self.assertIn(required, core_setup)
        for forbidden in ("XCODE_REQUIRED_MAJOR", "xcodebuild", "xcode-select --switch", "sudo "):
            self.assertNotIn(forbidden, core_setup)

    def test_native_core_targets_older_systems_and_needs_no_vc_runtime(self) -> None:
        cmake = text("software/recording_worker/native/CMakeLists.txt")
        self.assertIn('set(CMAKE_OSX_DEPLOYMENT_TARGET "13.0"', cmake)
        self.assertIn('set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded', cmake)
        self.assertLess(cmake.index("CMAKE_OSX_DEPLOYMENT_TARGET"), cmake.index("project("))

        script = text("tools/install-macos.sh")
        minimum = re.search(r"MACOS_MINIMUM_MAJOR=(\d+)", script)
        self.assertIsNotNone(minimum)
        self.assertEqual(minimum.group(1), "13")
        self.assertIn("macos_major < MACOS_MINIMUM_MAJOR", script)
        self.assertIn("if ((skip_recording_core == 0)); then", script)

    def test_release_workflow_installs_like_a_user_without_a_compiler(self) -> None:
        workflow = text(".github/workflows/release.yml")
        for required in (
            "native-core:",
            "needs: native-core",
            "--core-assets core-assets",
            "tools/setup_recording_worker.py --package",
            "minos 13.0",
            "DEVELOPER_DIR: /nonexistent-toolchain",
            "STUDY_RUNNER_CORE_ASSET_DIR",
            "bash tools/install-macos.sh",
            "tools\\install-windows.cmd",
            "test -f \"$source_root/study-runner-release.json\"",
            "test ! -e .venv/bin/cmake",
            "Reusing the current verified XDF recording core.",
            "study-runner-xdf-core-macos-x64.zip",
            "study-runner-xdf-core-macos-arm64.zip",
            "study-runner-xdf-core-windows-x64.zip",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("--install-system-dependencies", workflow)
        self.assertNotIn("xcodebuild", workflow)

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
            ".\\tools\\install-windows.cmd\n",
            ".\\tools\\start-windows.cmd",
            "bash tools/install-macos.sh\n",
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
            "macOS 13 or newer",
            "no Xcode",
            "study-runner-xdf-core-<platform>.zip",
            "software/constraints/uv-bootstrap.txt",
        ):
            self.assertIn(instruction, readme)
        self.assertLess(readme.index("install-windows.cmd"), readme.index("## Project Layout"))
        self.assertLess(readme.index("study-runner-source.zip"), readme.index("## Project Layout"))
        for outdated in (
            "-InstallSystemDependencies",
            "--install-system-dependencies",
            "Xcode 26.3",
            "xip --expand",
            "xcodebuild",
            "macOS 15.6",
        ):
            self.assertNotIn(outdated, readme)
        self.assertNotIn("brew install", readme.casefold())
        self.assertNotIn("brew.sh", readme.casefold())
        self.assertNotRegex(readme, r"(?m)^\s*(?:mas|xcodes)\s+install\b")

        german = text("docs/start-here.de.md")
        for instruction in (
            "study-runner-source.zip",
            "study-runner-source.tar.gz",
            ".\\tools\\install-windows.cmd\n",
            "bash tools/install-macos.sh\n",
            "Desktop-Verknuepfung erstellen",
            "Study Runner.command",
            "Ctrl+C",
            "macOS 13 oder neuer",
            "kein Xcode",
            "Wenn die Installation mit einem Fehler abbricht",
        ):
            self.assertIn(instruction, german)
        for outdated in (
            "-InstallSystemDependencies",
            "--install-system-dependencies",
            "xip --expand",
            "xcodebuild",
            "15.6",
        ):
            self.assertNotIn(outdated, german)
        self.assertNotRegex(german, r"(?m)^\s*(?:mas|xcodes)\s+install\b")
        # Archive users cannot git pull; maintainer commands belong to the release section.
        update = german[german.index("## Update am Nutzer-Rechner") :]
        self.assertIn("git pull` funktioniert in einem\nentpackten Archiv nicht", update)
        self.assertNotIn("release.ps1", update[: update.index("## Release-Zugang")])

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
