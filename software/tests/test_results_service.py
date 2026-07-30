from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services import results_service


class ResultsServicePathTests(unittest.TestCase):
    def test_project_root_points_to_software_folder(self) -> None:
        self.assertEqual(results_service._project_root().name, "software")

    def test_labrecorder_relative_path_resolves_under_software(self) -> None:
        resolved = results_service._resolve_project_path(
            "study_runner/integrations/brainbit/recordings",
            results_service._project_root(),
        )

        expected = (
            Path(__file__).resolve().parents[1]
            / "study_runner"
            / "integrations"
            / "brainbit"
            / "recordings"
        ).resolve()
        self.assertEqual(resolved, expected)

    def _capture_answer_details(self, result_payload, study_config):
        calls: list[tuple[float, float]] = []
        original = results_service._interval_summary_from_epochs

        def fake_summary(_hardware_config, start_epoch, end_epoch, context=None):
            calls.append((start_epoch, end_epoch))
            return {"brainbit": {"available": False}, "mini_radar": {"available": False}}

        results_service._interval_summary_from_epochs = fake_summary
        try:
            details = results_service.build_answer_details(result_payload, study_config, {})
        finally:
            results_service._interval_summary_from_epochs = original
        return details, calls

    @staticmethod
    def _iso_epoch(value: str) -> float:
        return results_service._parse_iso_timestamp(value).timestamp()

    def test_answer_details_use_card_events_for_question_and_stimulus_intervals(self) -> None:
        details, calls = self._capture_answer_details(
            {
                "participant_id": "p01",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:01:00Z",
                "answers": {"q1": 6},
                "answer_events": [
                    {
                        "question_index": 1,
                        "shown_at": "2026-01-01T10:00:10Z",
                        "answered_at": "2026-01-01T10:00:20Z",
                    }
                ],
                "card_events": [
                    {
                        "question_index": 0,
                        "question_type": "stimulus",
                        "shown_at": "2026-01-01T10:00:01Z",
                        "active_started_at": "2026-01-01T10:00:02Z",
                        "active_ended_at": "2026-01-01T10:00:09Z",
                    },
                    {
                        "question_index": 1,
                        "question_type": "likert",
                        "shown_at": "2026-01-01T10:00:10Z",
                        "answered_at": "2026-01-01T10:00:20Z",
                    },
                ],
            },
            {
                "questions": [
                    {"type": "stimulus", "title": "Look"},
                    {"type": "likert", "prompt": "How calm?"},
                    {"type": "finish"},
                ]
            },
        )

        self.assertEqual(details[0]["question_type"], "stimulus")
        self.assertEqual(details[0]["biosignal_interval_kind"], "stimulus_active")
        self.assertEqual(details[1]["biosignal_interval_kind"], "question_visible")
        self.assertEqual(details[0]["biosignal_interval_timing_source"], "client_clock")
        self.assertEqual(
            calls,
            [
                (self._iso_epoch("2026-01-01T10:00:02Z"), self._iso_epoch("2026-01-01T10:00:09Z")),
                (self._iso_epoch("2026-01-01T10:00:10Z"), self._iso_epoch("2026-01-01T10:00:20Z")),
            ],
        )

    def test_answer_details_apply_client_clock_offset_when_tablet_clock_is_skewed(self) -> None:
        # Tablet clock runs 90 s ahead of the server clock, so the submitted
        # offset (server = client + offset) is -90 000 ms. The sliced epochs
        # must land back on the server clock.
        details, calls = self._capture_answer_details(
            {
                "participant_id": "p01",
                "timestamp_start": "2026-01-01T10:01:30Z",
                "timestamp_end": "2026-01-01T10:02:30Z",
                "client_clock_offset_ms": -90_000,
                "answers": {"q0": 4},
                "answer_events": [],
                "card_events": [
                    {
                        "question_index": 0,
                        "question_type": "likert",
                        "shown_at": "2026-01-01T10:01:40Z",
                        "answered_at": "2026-01-01T10:01:50Z",
                    },
                ],
            },
            {"questions": [{"type": "likert", "prompt": "How calm?"}, {"type": "finish"}]},
        )

        self.assertEqual(details[0]["biosignal_interval_timing_source"], "client_clock_plus_offset")
        self.assertEqual(
            calls,
            [
                (
                    self._iso_epoch("2026-01-01T10:00:10Z"),
                    self._iso_epoch("2026-01-01T10:00:20Z"),
                ),
            ],
        )

    def test_answer_details_prefer_server_clock_epochs_over_client_timestamps(self) -> None:
        shown_epoch = self._iso_epoch("2026-01-01T10:00:10Z")
        answered_epoch = self._iso_epoch("2026-01-01T10:00:20Z")
        details, calls = self._capture_answer_details(
            {
                "participant_id": "p01",
                "timestamp_start": "2026-01-01T10:01:30Z",
                "timestamp_end": "2026-01-01T10:02:30Z",
                "answers": {"q0": 4},
                "answer_events": [],
                "card_events": [
                    {
                        "question_index": 0,
                        "question_type": "likert",
                        # Client wall clock is wildly wrong...
                        "shown_at": "2026-01-01T10:01:40Z",
                        "answered_at": "2026-01-01T10:01:50Z",
                        # ...but the client recorded server-clock epochs.
                        "shown_at_server_epoch_ms": shown_epoch * 1000,
                        "answered_at_server_epoch_ms": answered_epoch * 1000,
                    },
                ],
            },
            {"questions": [{"type": "likert", "prompt": "How calm?"}, {"type": "finish"}]},
        )

        self.assertEqual(details[0]["biosignal_interval_timing_source"], "server_clock")
        self.assertEqual(calls, [(shown_epoch, answered_epoch)])

    def test_answer_details_include_skipped_optional_questions(self) -> None:
        details, calls = self._capture_answer_details(
            {
                "participant_id": "p01",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:01:00Z",
                "answers": {"q1": 5},
                "answer_events": [],
                "card_events": [
                    {
                        "question_index": 0,
                        "question_type": "likert",
                        "shown_at": "2026-01-01T10:00:10Z",
                        "completed_at": "2026-01-01T10:00:20Z",
                    },
                    {
                        "question_index": 1,
                        "question_type": "likert",
                        "shown_at": "2026-01-01T10:00:20Z",
                        "answered_at": "2026-01-01T10:00:30Z",
                    },
                ],
            },
            {
                "questions": [
                    {"type": "likert", "prompt": "Optional", "required": False},
                    {"type": "likert", "prompt": "Required"},
                    {"type": "finish"},
                ]
            },
        )

        self.assertEqual(len(details), 2)
        self.assertTrue(details[0]["skipped"])
        self.assertIsNone(details[0]["answer"])
        self.assertEqual(details[0]["question_key"], "q0")
        self.assertEqual(calls[0], (self._iso_epoch("2026-01-01T10:00:10Z"), self._iso_epoch("2026-01-01T10:00:20Z")))

    def test_answer_details_do_not_mark_unshown_optional_questions_as_skipped(self) -> None:
        details, calls = self._capture_answer_details(
            {
                "participant_id": "p01",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:01:00Z",
                "answers": {"q0": 5},
                "answer_events": [],
                "card_events": [
                    {
                        "question_index": 0,
                        "question_type": "likert",
                        "shown_at": "2026-01-01T10:00:10Z",
                        "answered_at": "2026-01-01T10:00:20Z",
                    },
                ],
            },
            {
                "questions": [
                    {"type": "likert", "prompt": "Answered"},
                    {"type": "likert", "prompt": "Never shown", "required": False},
                    {"type": "finish"},
                ]
            },
        )

        self.assertEqual([detail["question_key"] for detail in details], ["q0"])
        self.assertEqual(len(calls), 1)

    def test_data_warnings_flag_gaps_dropouts_and_truncation(self) -> None:
        warnings = results_service._build_data_warnings(
            {
                "brainbit": {"enabled": True, "available": False},
                "mini_radar": {
                    "enabled": True,
                    "available": True,
                    "max_gap_seconds": 12.0,
                    "dropped_in_interval": 50,
                },
                "camera_emotion": {"available": False},  # disabled -> silent
            },
            interval_seconds=30.0,
        )

        self.assertEqual(len(warnings), 3)
        self.assertIn("BrainBit EEG: no data arrived", warnings[0])
        self.assertIn("data gap of 12.0 s", warnings[1])
        self.assertIn("50 radio packets were lost", warnings[2])

    def test_data_warnings_ignore_short_pauses(self) -> None:
        warnings = results_service._build_data_warnings(
            {
                "mini_radar": {
                    "enabled": True,
                    "available": True,
                    "max_gap_seconds": 2.0,
                    "dropped_in_interval": 0,
                },
            },
            interval_seconds=30.0,
        )

        self.assertEqual(warnings, [])

    def test_signal_sidecar_contains_card_events_and_sample_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = results_service._write_signal_sidecar(
                Path(tmpdir),
                "p01_mr60_signals",
                "mr60",
                {
                    "study_id": "Study",
                    "participant_id": "p01",
                    "timestamp_start": "2026-01-01T10:00:00Z",
                    "timestamp_end": "2026-01-01T10:01:00Z",
                    "card_events": [
                        {
                            "question_index": 0,
                            "question_type": "stimulus",
                            "shown_at": "2026-01-01T10:00:01Z",
                        }
                    ],
                },
                [{"server_received_epoch": 1.0, "heartRate": 72.0}],
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["sensor"], "mr60")
        self.assertEqual(payload["sample_count"], 1)
        self.assertEqual(payload["card_events"][0]["question_type"], "stimulus")


if __name__ == "__main__":
    unittest.main()
