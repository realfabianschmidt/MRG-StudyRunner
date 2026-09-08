"""Package 5b preflight: system clock plausibility and time-sync-service evidence.

Neither signal alone is trusted -- see system_clock_probe.py's own docstring
for why. These tests confirm that independence directly: a plausible clock
with no running sync service must fail, and vice versa.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.shared.system_clock_probe import (
    MAX_PLAUSIBLE_EPOCH_SECONDS,
    MIN_PLAUSIBLE_EPOCH_SECONDS,
    probe_system_clock,
)


def _fake_run(returncode: int = 0, stdout: str = "Running"):
    def run(*_args, **_kwargs):
        return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": ""})()

    return run


class SystemClockProbeTests(unittest.TestCase):
    def test_plausible_clock_and_running_service_is_ok(self) -> None:
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="win32",
            run=_fake_run(),
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["plausible"])
        self.assertTrue(result["service_running"])
        self.assertIsNone(result["reason"])

    def test_clock_before_the_floor_is_not_plausible(self) -> None:
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS - 1.0,
            platform_name="win32",
            run=_fake_run(),
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["plausible"])
        self.assertIn("before the plausible floor", result["reason"])

    def test_clock_beyond_the_ceiling_is_not_plausible(self) -> None:
        result = probe_system_clock(
            now=lambda: MAX_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="win32",
            run=_fake_run(),
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["plausible"])
        self.assertIn("beyond the plausible ceiling", result["reason"])

    def test_plausible_clock_but_no_running_service_is_not_ok(self) -> None:
        """A believable timestamp alone proves nothing about drift."""
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="win32",
            run=_fake_run(returncode=0, stdout="Stopped"),
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["plausible"])
        self.assertFalse(result["service_running"])
        self.assertIsNotNone(result["reason"])

    def test_running_service_but_implausible_clock_is_not_ok(self) -> None:
        """A running sync service does not prove this reading is correct."""
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS - 1.0,
            platform_name="win32",
            run=_fake_run(),
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["plausible"])
        self.assertTrue(result["service_running"])

    def test_windows_status_is_read_from_a_dotnet_enum_not_parsed_text(self) -> None:
        """PowerShell's Get-Service .Status is a ServiceControllerStatus enum:
        printing it always yields the English member name, regardless of the
        machine's display language. This is why the probe asks for that one
        property instead of parsing `sc query`'s localized free-text output --
        confirmed by construction here (whitespace-padded, but never a
        translated word)."""
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="win32",
            run=_fake_run(stdout="  Running  \r\n"),
        )
        self.assertTrue(result["service_running"])

    def test_windows_query_failure_reports_unknown_not_running(self) -> None:
        def raising_run(*_args, **_kwargs):
            raise OSError("sc not found")

        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="win32",
            run=raising_run,
        )
        self.assertIsNone(result["service_running"])
        self.assertFalse(result["ok"])

    def test_macos_uses_launchctl_exit_code(self) -> None:
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="darwin",
            run=_fake_run(returncode=0),
        )
        self.assertTrue(result["service_running"])

    def test_macos_non_zero_exit_is_not_running(self) -> None:
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="darwin",
            run=_fake_run(returncode=1),
        )
        self.assertFalse(result["service_running"])
        self.assertFalse(result["ok"])

    def test_linux_is_fail_closed_regardless_of_clock(self) -> None:
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="linux",
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["service_running"])
        self.assertIn("fail-closed", result["reason"])

    def test_unrecognized_platform_reports_no_defined_check(self) -> None:
        result = probe_system_clock(
            now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
            platform_name="some-exotic-os",
        )
        self.assertFalse(result["ok"])
        self.assertIsNone(result["service_running"])
        self.assertIn("No time-service check is defined", result["reason"])

    def test_never_raises_on_a_subprocess_error(self) -> None:
        def raising_run(*_args, **_kwargs):
            raise OSError("no such command")

        try:
            result = probe_system_clock(
                now=lambda: MIN_PLAUSIBLE_EPOCH_SECONDS + 1.0,
                platform_name="darwin",
                run=raising_run,
            )
        except Exception as error:  # noqa: BLE001 - the point of this test
            self.fail(f"probe_system_clock raised instead of reporting: {error}")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
