from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.validation import (
    ValidationError,
    validate_and_normalize_config,
    validate_and_normalize_results,
    validate_and_normalize_trial_options,
)


class ValidationTests(unittest.TestCase):
    def test_participant_id_text_fields_are_preserved(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Participant Text Test",
                "questions": [
                    {
                        "type": "participant-id",
                        "prompt": "Please identify yourself anonymously.",
                        "code_label": "Your code",
                        "info_top": "The raw inputs are hashed locally.",
                    },
                    {
                        "type": "finish",
                        "title": "Done",
                        "prompt": "Saved.",
                    },
                ],
            }
        )

        participant_card = config["questions"][0]
        self.assertEqual(participant_card["code_label"], "Your code")
        self.assertEqual(participant_card["info_top"], "The raw inputs are hashed locally.")
        self.assertNotIn("code_hint", participant_card)

    def test_participant_id_legacy_code_hint_migrates_to_info_top(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Legacy Hint",
                "questions": [
                    {
                        "type": "participant-id",
                        "prompt": "Identify yourself.",
                        "code_hint": "Hashed locally on this device.",
                    },
                    {"type": "finish", "title": "Done", "prompt": "Saved."},
                ],
            }
        )

        participant_card = config["questions"][0]
        self.assertEqual(participant_card["info_top"], "Hashed locally on this device.")
        self.assertNotIn("code_hint", participant_card)

    def test_card_info_text_round_trips_and_omits_empty(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Card Info",
                "questions": [
                    {
                        "type": "likert",
                        "prompt": "How relaxed do you feel?",
                        "info_top": "Read the scale before answering.",
                        "info_bottom": "There are no right or wrong answers.",
                    },
                    {
                        "type": "likert",
                        "prompt": "And now?",
                        "info_top": "   ",
                    },
                ],
            }
        )

        first = config["questions"][0]
        self.assertEqual(first["info_top"], "Read the scale before answering.")
        self.assertEqual(first["info_bottom"], "There are no right or wrong answers.")

        second = config["questions"][1]
        self.assertNotIn("info_top", second)
        self.assertNotIn("info_bottom", second)

    def test_participant_id_default_field_config_is_private_for_names(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Participant Defaults",
                "questions": [
                    {"type": "participant-id", "prompt": "Identify yourself."},
                    {"type": "finish", "title": "Done", "prompt": "Saved."},
                ],
            }
        )

        fields = config["questions"][0]["fields"]
        self.assertEqual(fields["first_name"], {"enabled": True, "use_for_key": True, "store": False})
        self.assertEqual(fields["last_name"], {"enabled": True, "use_for_key": True, "store": False})
        self.assertEqual(fields["age_group"], {"enabled": True, "use_for_key": True, "store": True, "options": ["18-25", "26-35", "36-45", "46-60", "60+"]})
        self.assertEqual(fields["childhood_area"], {"enabled": True, "use_for_key": True, "store": True})
        self.assertEqual(fields["childhood_nearest_city"], {"enabled": True, "use_for_key": True, "store": True})
        # New fields default to disabled so existing studies are unchanged.
        self.assertEqual(fields["gender"], {"enabled": False, "use_for_key": False, "store": False, "options": ["Female", "Male", "Non-binary", "Prefer not to say"]})
        self.assertEqual(fields["birth_place"], {"enabled": False, "use_for_key": False, "store": False})
        self.assertEqual(fields["birth_date"], {"enabled": False, "use_for_key": False, "store": False})

    def test_participant_id_needs_at_least_one_key_field(self) -> None:
        with self.assertRaises(ValidationError):
            validate_and_normalize_config(
                {
                    "study_id": "No Key",
                    "questions": [
                        {
                            "type": "participant-id",
                            "fields": {
                                "first_name": {"enabled": True, "use_for_key": False, "store": False},
                                "last_name": {"enabled": True, "use_for_key": False, "store": False},
                                "age_group": {"enabled": True, "use_for_key": False, "store": True},
                                "childhood_area": {"enabled": True, "use_for_key": False, "store": True},
                                "childhood_nearest_city": {"enabled": True, "use_for_key": False, "store": True},
                            },
                        },
                        {"type": "finish"},
                    ],
                }
            )

    def test_result_accepts_configured_participant_metadata(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Metadata Study",
                "questions": [
                    {"type": "participant-id", "prompt": "Identify yourself."},
                    {"type": "finish"},
                ],
            }
        )

        result = validate_and_normalize_results(
            {
                "participant_id": "abc123",
                "study_id": "Metadata Study",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:01:00Z",
                "answers": {},
                "participant_metadata": {
                    "age_group": "18-25",
                    "childhood_area": "urban",
                    "childhood_nearest_city": "Munich",
                },
            },
            config,
        )

        self.assertEqual(result["participant_metadata"]["age_group"], "18-25")
        self.assertEqual(result["participant_metadata"]["childhood_area"], "urban")
        self.assertEqual(result["participant_metadata"]["childhood_nearest_city"], "Munich")

    def test_result_rejects_unstored_participant_metadata(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Metadata Study",
                "questions": [
                    {"type": "participant-id", "prompt": "Identify yourself."},
                    {"type": "finish"},
                ],
            }
        )

        with self.assertRaises(ValidationError):
            validate_and_normalize_results(
                {
                    "participant_id": "abc123",
                    "study_id": "Metadata Study",
                    "timestamp_start": "2026-01-01T10:00:00Z",
                    "timestamp_end": "2026-01-01T10:01:00Z",
                    "answers": {},
                    "participant_metadata": {
                        "first_name": "Anna",
                        "age_group": "18-25",
                        "childhood_area": "urban",
                        "childhood_nearest_city": "Munich",
                    },
                },
                config,
            )

    def test_result_accepts_card_events_for_questions_and_stimuli(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Biosignal Events",
                "questions": [
                    {"type": "stimulus", "title": "Look", "duration_ms": 1000},
                    {"type": "likert", "prompt": "How calm?", "scale": 7},
                    {"type": "finish"},
                ],
            }
        )

        result = validate_and_normalize_results(
            {
                "participant_id": "abc123",
                "study_id": "Biosignal Events",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:01:00Z",
                "answers": {"q1": 5},
                "participant_metadata": {},
                "answer_events": [
                    {
                        "question_index": 1,
                        "question_type": "likert",
                        "answer_key": "q1",
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
            config,
        )

        self.assertEqual(len(result["card_events"]), 2)
        self.assertEqual(result["card_events"][0]["active_started_at"], "2026-01-01T10:00:02Z")

    def test_trial_options_accept_marker_metadata_and_epoch_trigger(self) -> None:
        options = validate_and_normalize_trial_options(
            {
                "study_id": "Study A",
                "participant_id": "p01",
                "question_index": 2,
                "question_type": "stimulus",
                "phase": "stimulus_active_start",
                "marker_event": "stimulus_active_start",
                "client_trigger_epoch_ms": 1760000000123.4,
            }
        )

        self.assertEqual(options["study_id"], "Study A")
        self.assertEqual(options["question_index"], 2)
        self.assertEqual(options["question_type"], "stimulus")
        self.assertEqual(options["marker_event"], "stimulus_active_start")

    def test_study_sensor_defaults_preserve_legacy_biosignal_studies(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Legacy Sensors",
                "study_settings": {"sensors_enabled": True},
                "questions": [{"type": "finish"}],
            }
        )

        self.assertEqual(
            config["study_settings"]["sensors"],
            {"brainbit": True, "mini_radar": True, "camera_emotion": False},
        )

    def test_study_sensor_master_switch_disables_all_sensors(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "No Sensors",
                "study_settings": {
                    "sensors_enabled": False,
                    "sensors": {
                        "brainbit": True,
                        "mini_radar": True,
                        "camera_emotion": True,
                    },
                },
                "questions": [{"type": "finish"}],
            }
        )

        self.assertEqual(
            config["study_settings"]["sensors"],
            {"brainbit": False, "mini_radar": False, "camera_emotion": False},
        )

    def test_study_sensor_selection_rejects_unknown_sensor_keys(self) -> None:
        with self.assertRaises(ValidationError):
            validate_and_normalize_config(
                {
                    "study_id": "Unknown Sensor",
                    "study_settings": {
                        "sensors": {
                            "brainbit": True,
                            "unknown_sensor": True,
                        },
                    },
                    "questions": [{"type": "finish"}],
                }
            )

    def test_participant_configurable_options_and_new_fields(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Configured Fields",
                "questions": [
                    {
                        "type": "participant-id",
                        "prompt": "Identify yourself.",
                        "fields": {
                            "first_name": {"enabled": True, "use_for_key": True, "store": False},
                            "age_group": {
                                "enabled": True,
                                "use_for_key": False,
                                "store": True,
                                "options": ["young", "old", "young", "  "],
                            },
                            "gender": {
                                "enabled": True,
                                "use_for_key": False,
                                "store": True,
                                "options": ["Woman", "Man", "Other"],
                            },
                            "birth_place": {"enabled": True, "use_for_key": False, "store": True},
                            "birth_date": {"enabled": True, "use_for_key": False, "store": True},
                            "childhood_area": {"enabled": False},
                            "childhood_nearest_city": {"enabled": False},
                        },
                    },
                    {"type": "finish"},
                ],
            }
        )

        fields = config["questions"][0]["fields"]
        # Options are cleaned (deduped, blanks dropped).
        self.assertEqual(fields["age_group"]["options"], ["young", "old"])
        self.assertEqual(fields["gender"]["options"], ["Woman", "Man", "Other"])

        result = validate_and_normalize_results(
            {
                "participant_id": "abc123",
                "study_id": "Configured Fields",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:01:00Z",
                "answers": {},
                "participant_metadata": {
                    "age_group": "old",
                    "gender": "Woman",
                    "birth_place": "Berlin",
                    "birth_date": "1990-05-14",
                },
            },
            config,
        )
        self.assertEqual(result["participant_metadata"]["gender"], "Woman")
        self.assertEqual(result["participant_metadata"]["birth_date"], "1990-05-14")

    def test_participant_metadata_rejects_value_outside_configured_options(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Configured Fields",
                "questions": [
                    {
                        "type": "participant-id",
                        "prompt": "Identify yourself.",
                        "fields": {
                            "first_name": {"enabled": True, "use_for_key": True, "store": False},
                            "gender": {
                                "enabled": True,
                                "use_for_key": False,
                                "store": True,
                                "options": ["Woman", "Man"],
                            },
                            "age_group": {"enabled": False},
                            "childhood_area": {"enabled": False},
                            "childhood_nearest_city": {"enabled": False},
                        },
                    },
                    {"type": "finish"},
                ],
            }
        )

        with self.assertRaises(ValidationError):
            validate_and_normalize_results(
                {
                    "participant_id": "abc123",
                    "study_id": "Configured Fields",
                    "timestamp_start": "2026-01-01T10:00:00Z",
                    "timestamp_end": "2026-01-01T10:01:00Z",
                    "answers": {},
                    "participant_metadata": {"gender": "Nonbinary"},
                },
                config,
            )

    def test_participant_birth_date_rejects_garbage(self) -> None:
        config = validate_and_normalize_config(
            {
                "study_id": "Birth Date Study",
                "questions": [
                    {
                        "type": "participant-id",
                        "prompt": "Identify yourself.",
                        "fields": {
                            "first_name": {"enabled": True, "use_for_key": True, "store": False},
                            "birth_date": {"enabled": True, "use_for_key": False, "store": True},
                            "age_group": {"enabled": False},
                            "childhood_area": {"enabled": False},
                            "childhood_nearest_city": {"enabled": False},
                        },
                    },
                    {"type": "finish"},
                ],
            }
        )

        with self.assertRaises(ValidationError):
            validate_and_normalize_results(
                {
                    "participant_id": "abc123",
                    "study_id": "Birth Date Study",
                    "timestamp_start": "2026-01-01T10:00:00Z",
                    "timestamp_end": "2026-01-01T10:01:00Z",
                    "answers": {},
                    "participant_metadata": {"birth_date": "not-a-date"},
                },
                config,
            )


if __name__ == "__main__":
    unittest.main()
