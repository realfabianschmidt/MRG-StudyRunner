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

    def test_answer_details_use_card_events_for_question_and_stimulus_intervals(self) -> None:
        calls: list[tuple[str, str]] = []
        original = results_service.build_interval_biosignal_summary

        def fake_summary(_hardware_config, interval_start, interval_end, context=None):
            calls.append((interval_start, interval_end))
            return {"brainbit": {"available": False}, "mini_radar": {"available": False}}

        results_service.build_interval_biosignal_summary = fake_summary
        try:
            details = results_service.build_answer_details(
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
                {},
            )
        finally:
            results_service.build_interval_biosignal_summary = original

        self.assertEqual(details[0]["question_type"], "stimulus")
        self.assertEqual(details[0]["biosignal_interval_kind"], "stimulus_active")
        self.assertEqual(details[1]["biosignal_interval_kind"], "question_visible")
        self.assertEqual(
            calls,
            [
                ("2026-01-01T10:00:02Z", "2026-01-01T10:00:09Z"),
                ("2026-01-01T10:00:10Z", "2026-01-01T10:00:20Z"),
            ],
        )

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
