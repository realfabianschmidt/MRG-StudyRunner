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


if __name__ == "__main__":
    unittest.main()
