from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.backend.services.validation import validate_and_normalize_config
from study_runner.integrations.notion_upload import adapter


class NotionParticipantMetadataTests(unittest.TestCase):
    def _config(self) -> dict:
        return validate_and_normalize_config(
            {
                "study_id": "Notion Metadata",
                "questions": [
                    {"type": "participant-id", "prompt": "Identify yourself."},
                    {"type": "finish"},
                ],
            }
        )

    def test_metadata_schema_uses_only_stored_fields_by_default(self) -> None:
        schema = adapter._build_participant_metadata_schema(self._config())

        self.assertNotIn("First Name", schema)
        self.assertNotIn("Last Name", schema)
        self.assertEqual(schema["Age Group"], {"select": {}})
        self.assertEqual(schema["Childhood Area"], {"select": {}})
        self.assertEqual(schema["Childhood Nearest City"], {"rich_text": {}})

    def test_metadata_schema_includes_names_when_configured_for_storage(self) -> None:
        config = self._config()
        config["questions"][0]["fields"]["first_name"]["store"] = True

        schema = adapter._build_participant_metadata_schema(config)

        self.assertEqual(schema["First Name"], {"rich_text": {}})

    def test_metadata_properties_match_notion_property_types(self) -> None:
        props = adapter._build_participant_metadata_properties(
            {
                "participant_metadata": {
                    "age_group": "18-25",
                    "childhood_area": "urban",
                    "childhood_nearest_city": "Munich",
                }
            },
            self._config(),
        )

        self.assertEqual(props["Age Group"]["select"]["name"], "18-25")
        self.assertEqual(props["Childhood Area"]["select"]["name"], "urban")
        self.assertEqual(
            props["Childhood Nearest City"]["rich_text"][0]["text"]["content"],
            "Munich",
        )

    def test_optional_metadata_fields_have_complete_notion_mappings(self) -> None:
        config = self._config()
        fields = config["questions"][0]["fields"]
        for field_key in ("gender", "birth_place", "birth_date"):
            fields[field_key]["enabled"] = True
            fields[field_key]["store"] = True

        schema = adapter._build_participant_metadata_schema(config)
        props = adapter._build_participant_metadata_properties(
            {
                "participant_metadata": {
                    "gender": "Non-binary",
                    "birth_place": "Berlin",
                    "birth_date": "1990-05-04",
                }
            },
            config,
        )

        self.assertEqual(schema["Gender"], {"select": {}})
        self.assertEqual(schema["Birth Place"], {"rich_text": {}})
        self.assertEqual(schema["Birth Date"], {"date": {}})
        self.assertEqual(props["Gender"], {"select": {"name": "Non-binary"}})
        self.assertEqual(
            props["Birth Place"]["rich_text"][0]["text"]["content"],
            "Berlin",
        )
        self.assertEqual(props["Birth Date"], {"date": {"start": "1990-05-04"}})

    def test_answer_detail_format_includes_stimulus_interval_and_biomarkers(self) -> None:
        lines = adapter._format_answer_details(
            [
                {
                    "question_number": 1,
                    "question_type": "stimulus",
                    "question_prompt": "Observe",
                    "answer": "stimulus",
                    "biosignal_interval_kind": "stimulus_active",
                    "interval_seconds": 7.0,
                    "biosignal_interval": {
                        "brainbit": {
                            "available": True,
                            "avg_attention": 0.4,
                            "avg_relaxation": 0.6,
                            "avg_delta": 1.0,
                            "avg_theta": 2.0,
                            "avg_alpha": 3.0,
                            "avg_beta": 4.0,
                            "avg_gamma": 5.0,
                        },
                        "mini_radar": {
                            "available": True,
                            "avg_heart_rate": 72.0,
                            "avg_breath_rate": 12.0,
                            "avg_quality": 0.8,
                            "avg_distance": 150.0,
                        },
                    },
                }
            ]
        )

        self.assertIn("Stimulus", lines[0])
        self.assertIn("stimulus_active", lines[0])
        self.assertIn("gamma=5.00", lines[0])
        self.assertIn("dist=150.00", lines[0])


if __name__ == "__main__":
    unittest.main()
