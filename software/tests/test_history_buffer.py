from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework.history_buffer import (
    BUFFER_SECONDS_ENV_VAR,
    history_maxlen,
    samples_in_interval,
    truncation_info,
)


class HistoryMaxlenTests(unittest.TestCase):
    def test_default_fits_two_hours(self) -> None:
        self.assertEqual(history_maxlen(10.0), 72_000)

    def test_env_var_overrides_session_length(self) -> None:
        with patch.dict(os.environ, {BUFFER_SECONDS_ENV_VAR: "600"}):
            self.assertEqual(history_maxlen(10.0), 6_000)

    def test_never_shrinks_below_previous_default(self) -> None:
        with patch.dict(os.environ, {BUFFER_SECONDS_ENV_VAR: "10"}):
            self.assertEqual(history_maxlen(1.0), 4096)

    def test_invalid_env_value_falls_back_to_default(self) -> None:
        with patch.dict(os.environ, {BUFFER_SECONDS_ENV_VAR: "not-a-number"}):
            self.assertEqual(history_maxlen(10.0), 72_000)


class TruncationInfoTests(unittest.TestCase):
    def test_partial_buffer_never_reports_overflow(self) -> None:
        history = deque(maxlen=10)
        history.extend({"_epoch": float(epoch)} for epoch in range(100, 105))

        info = truncation_info(history, start_epoch=1.0)

        self.assertEqual(info, {"buffer_overflowed": False})

    def test_full_buffer_flags_requests_older_than_retained_data(self) -> None:
        history = deque(maxlen=5)
        history.extend({"_epoch": float(epoch)} for epoch in range(100, 110))

        info = truncation_info(history, start_epoch=90.0)

        self.assertTrue(info["buffer_overflowed"])
        self.assertEqual(info["earliest_retained_epoch"], 105.0)

    def test_full_buffer_with_window_inside_retained_data_is_clean(self) -> None:
        history = deque(maxlen=5)
        history.extend({"_epoch": float(epoch)} for epoch in range(100, 110))

        info = truncation_info(history, start_epoch=106.0)

        self.assertEqual(info, {"buffer_overflowed": False})


class SamplesInIntervalTests(unittest.TestCase):
    def test_inclusive_window(self) -> None:
        history = deque({"_epoch": float(epoch)} for epoch in range(1, 6))

        samples = samples_in_interval(history, 2.0, 4.0)

        self.assertEqual([sample["_epoch"] for sample in samples], [2.0, 3.0, 4.0])


class AdapterBufferTests(unittest.TestCase):
    def test_adapter_summaries_report_truncation(self) -> None:
        from study_runner.plugins.mr60_mini_radar import adapter as mr60_adapter

        original = mr60_adapter._history
        mr60_adapter._history = deque(maxlen=3)
        try:
            mr60_adapter._history.extend(
                {"_epoch": float(epoch), "heartRate": 70.0, "total_dropped": 0}
                for epoch in range(100, 110)
            )
            summary = mr60_adapter.get_interval_summary(50.0, 60.0)
        finally:
            mr60_adapter._history = original

        self.assertFalse(summary["available"])
        self.assertTrue(summary["buffer_overflowed"])
        self.assertEqual(summary["earliest_retained_epoch"], 107.0)


if __name__ == "__main__":
    unittest.main()
