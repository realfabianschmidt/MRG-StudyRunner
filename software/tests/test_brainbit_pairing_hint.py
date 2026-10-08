"""A Windows pairing hides the BrainBit from the vendor SDK's scan.

The plugin's README names it the most common reason a band is "not found";
these tests hold the adapter to telling the operator exactly that, without
ever running PowerShell for real.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.sensors.brainbit import adapter
from study_runner.plugins.sensors.brainbit.brainbit_realtime_cli import (
    EXIT_CONNECT_FAILED,
    EXIT_NO_DEVICE_FOUND,
)


def _powershell(count: str):
    return SimpleNamespace(returncode=0, stdout=f"{count}\n")


class BrainBitPairingHintTests(unittest.TestCase):
    def setUp(self) -> None:
        adapter._pairing_check = None
        self.addCleanup(setattr, adapter, "_pairing_check", None)

    def test_a_paired_band_turns_not_found_into_the_pairing_hint(self) -> None:
        with patch.object(adapter.sys, "platform", "win32"), \
                patch.object(adapter.subprocess, "run", return_value=_powershell("1")) as run:
            reason = adapter._operator_reason(EXIT_NO_DEVICE_FOUND)
        self.assertEqual(reason["detail_key"], "brainbit.error.pairedInWindows")
        self.assertTrue(reason["pairing_conflict"])
        self.assertFalse(reason["retry"])
        self.assertIn("Get-PnpDevice", run.call_args.args[0][-1])

    def test_an_unpaired_band_keeps_the_ordinary_not_found_message(self) -> None:
        with patch.object(adapter.sys, "platform", "win32"), \
                patch.object(adapter.subprocess, "run", return_value=_powershell("0")):
            reason = adapter._operator_reason(EXIT_NO_DEVICE_FOUND)
        self.assertEqual(reason["detail_key"], "brainbit.error.deviceNotFound")

    def test_other_failures_never_run_the_check(self) -> None:
        with patch.object(adapter.sys, "platform", "win32"), patch.object(adapter.subprocess, "run") as run:
            adapter._operator_reason(EXIT_CONNECT_FAILED)
        run.assert_not_called()

    def test_restart_decisions_never_depend_on_this_computers_bluetooth(self) -> None:
        """The watchdog uses _exit_reason; it must stay a pure mapping."""
        with patch.object(adapter.sys, "platform", "win32"), patch.object(adapter.subprocess, "run") as run:
            reason = adapter._exit_reason(EXIT_NO_DEVICE_FOUND)
        run.assert_not_called()
        self.assertEqual(reason["detail_key"], "brainbit.error.deviceNotFound")

    def test_the_check_is_skipped_off_windows(self) -> None:
        with patch.object(adapter.sys, "platform", "darwin"), patch.object(adapter.subprocess, "run") as run:
            self.assertFalse(adapter._windows_pairing_conflict())
        run.assert_not_called()

    def test_a_failing_query_is_not_a_finding(self) -> None:
        with patch.object(adapter.sys, "platform", "win32"), \
                patch.object(adapter.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell", 8)):
            self.assertFalse(adapter._windows_pairing_conflict())

    def test_the_answer_is_cached_between_retries(self) -> None:
        with patch.object(adapter.sys, "platform", "win32"), \
                patch.object(adapter.subprocess, "run", return_value=_powershell("1")) as run:
            adapter._windows_pairing_conflict()
            adapter._windows_pairing_conflict()
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
