"""Every validation finding is either blocking or an accept-able warning.

Operators may accept quality warnings with a reason and let processing
continue; blocking findings stop derived artifacts from being built. A new
check whose code is in neither group would silently count as blocking, so
this test makes the author classify it.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.data_core.host.recording_quality import (
    BLOCKING_ISSUE_CODES,
    QUALITY_WARNING_CODES,
    issue_is_blocking,
    split_validation_issues,
)
from study_runner.data_core.host.xdf import ValidationIssue
from study_runner.runtime_core.delivery.finalization_service import (
    _error_has_only_quality_warnings,
)

SOURCE_ROOT = PROJECT_ROOT / "study_runner"


class RecordingIssueClassTests(unittest.TestCase):
    def test_every_emitted_code_belongs_to_exactly_one_group(self) -> None:
        emitted: set[str] = set()
        for folder in ("data_core", "runtime_core"):
            for path in (SOURCE_ROOT / folder).rglob("*.py"):
                emitted.update(re.findall(r'code="([a-z_]+)"', path.read_text(encoding="utf-8")))
        self.assertTrue(emitted)
        self.assertFalse(BLOCKING_ISSUE_CODES & QUALITY_WARNING_CODES)
        unclassified = sorted(emitted - BLOCKING_ISSUE_CODES - QUALITY_WARNING_CODES)
        self.assertEqual(unclassified, [], "classify these codes in recording_quality.py")

    def test_unknown_codes_block(self) -> None:
        self.assertTrue(issue_is_blocking("a_future_check"))
        self.assertFalse(issue_is_blocking("insufficient_time_coverage"))

    def test_split_separates_warnings_from_blocking_findings(self) -> None:
        blocking, warnings = split_validation_issues(
            [
                ValidationIssue(code="insufficient_time_coverage", message="late"),
                ValidationIssue(code="unreadable_source", message="broken"),
            ]
        )
        self.assertEqual([issue.code for issue in blocking], ["unreadable_source"])
        self.assertEqual([issue.code for issue in warnings], ["insufficient_time_coverage"])

    def test_recorded_error_text_is_classified(self) -> None:
        # The exact error text stored for the session of 2026-09-28.
        recorded = (
            "XDF source validation failed: insufficient_time_coverage: stream "
            "'study_runner.brainbit.bands' does not cover the marker-defined session window; "
            "insufficient_time_coverage: stream 'study_runner.brainbit.mental' does not cover "
            "the marker-defined session window"
        )
        self.assertTrue(_error_has_only_quality_warnings(recorded))
        self.assertFalse(
            _error_has_only_quality_warnings(
                "XDF source validation failed: unreadable_source: truncated; "
                "insufficient_time_coverage: late"
            )
        )
        self.assertFalse(_error_has_only_quality_warnings("worker crashed"))


if __name__ == "__main__":
    unittest.main()
