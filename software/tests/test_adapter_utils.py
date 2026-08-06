from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugin_framework.adapter_utils import config_section, set_state, timestamp
from study_runner.plugin_framework.plugin_api import PluginContext


class AdapterUtilsTests(unittest.TestCase):
    def test_config_section_uses_first_non_empty_dictionary(self) -> None:
        context = PluginContext(
            base_dir=PROJECT_ROOT,
            data_dir=PROJECT_ROOT / "saved_results",
            hardware_config={
                "preferred": {},
                "legacy": {"enabled": True},
                "invalid": "not-a-dict",
            },
            local_secrets={},
            local_secrets_file=PROJECT_ROOT / "local_secrets.json",
        )

        self.assertEqual(
            config_section(context, "preferred", "legacy"),
            {"enabled": True},
        )
        self.assertEqual(config_section(context, "invalid", "missing"), {})

    def test_set_state_updates_under_lock_and_adds_timestamp(self) -> None:
        state = {"status": "waiting"}

        with patch(
            "study_runner.plugin_framework.adapter_utils.timestamp",
            return_value="2026-07-30 13:30:00",
        ):
            set_state(
                state,
                threading.Lock(),
                {"status": "connected", "samples": 4},
            )

        self.assertEqual(
            state,
            {
                "status": "connected",
                "samples": 4,
                "updated_at": "2026-07-30 13:30:00",
            },
        )

    def test_timestamp_respects_explicit_epoch_zero(self) -> None:
        with patch(
            "study_runner.plugin_framework.adapter_utils.time.localtime",
            return_value="local-time",
        ) as localtime, patch(
            "study_runner.plugin_framework.adapter_utils.time.strftime",
            return_value="formatted",
        ) as strftime:
            self.assertEqual(timestamp(0), "formatted")

        localtime.assert_called_once_with(0.0)
        strftime.assert_called_once_with("%Y-%m-%d %H:%M:%S", "local-time")


if __name__ == "__main__":
    unittest.main()
