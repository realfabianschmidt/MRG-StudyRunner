from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from flask import Flask


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
            ],
            include_legacy_sensor_summaries=True,
        )

        self.assertIn("Stimulus", lines[0])
        self.assertIn("stimulus_active", lines[0])
        self.assertIn("Legacy-RAM-Snapshot (nicht kanonisch)", lines[0])
        self.assertIn("gamma=5.00", lines[0])
        self.assertIn("dist=150.00", lines[0])

    def test_auto_created_target_persists_only_canonical_plugin_settings(self) -> None:
        app = Flask(__name__)
        app.config["CONFIG_FILE"] = Path("active.study-runner")
        app.config["SAVED_STUDIES_DIR"] = Path("studies")
        projected = {
            "study_id": "Notion Metadata",
            "study_settings": {
                "notion_enabled": True,
                "notion_parent_page_id": "parent-1",
                "notion_database_id": "created-db",
                "notion_data_source_id": "created-source",
                "plugins": {
                    "notion": {
                        "enabled": True,
                        "required": False,
                        "settings": {"parent_page_id": "parent-1"},
                    }
                },
            },
        }
        with (
            app.app_context(),
            patch(
                "study_runner.backend.services.study_config_service.save_config"
            ) as save_config,
            patch(
                "study_runner.backend.services.study_config_service.save_study"
            ) as save_study,
        ):
            adapter._persist_study_database_id(projected)

        persisted = save_config.call_args.args[1]
        settings = persisted["study_settings"]
        self.assertNotIn("notion_enabled", settings)
        self.assertNotIn("notion_parent_page_id", settings)
        self.assertNotIn("notion_database_id", settings)
        self.assertNotIn("notion_data_source_id", settings)
        self.assertEqual(
            settings["plugins"]["notion"]["settings"],
            {
                "parent_page_id": "parent-1",
                "database_id": "created-db",
                "data_source_id": "created-source",
            },
        )
        self.assertEqual(save_study.call_args.args[1], persisted)

    def test_canonical_answer_format_ignores_embedded_ram_biomarkers(self) -> None:
        lines = adapter._format_answer_details(
            [
                {
                    "question_number": 1,
                    "question_type": "likert",
                    "question_prompt": "How calm?",
                    "answer": 4,
                    "interval_seconds": 7.0,
                    "biosignal_interval": {
                        "brainbit": {"available": True, "avg_attention": 12345.0}
                    },
                }
            ]
        )

        self.assertIn("Antwort: 4", lines[0])
        self.assertNotIn("12345", lines[0])
        self.assertNotIn("Biomarker", lines[0])
        self.assertNotIn("Legacy-RAM", lines[0])

    def test_answer_detail_format_renders_skipped_answers_as_dash(self) -> None:
        lines = adapter._format_answer_details(
            [
                {
                    "question_number": 2,
                    "question_type": "likert",
                    "question_prompt": "Optional rating",
                    "answer": None,
                    "skipped": True,
                    "biosignal_interval_kind": "question_visible",
                    "interval_seconds": 3.0,
                }
            ],
        )

        self.assertIn("Antwort: \u2014", lines[0])

    def test_card_summary_renders_unknown_plugins_without_core_changes(self) -> None:
        lines = adapter._format_card_summary(
            {
                "cards": [
                    {
                        "question_index": 3,
                        "streams": {
                            "future.metrics": {
                                "plugin_key": "future_sensor",
                                "count": 10,
                                "valid_count": 9,
                                "coverage": 0.9,
                                "missing_count": 1,
                                "drop_count": 2,
                                "max_gap_seconds": 0.3,
                                "channels": {
                                    "temperature": {
                                        "kind": "numeric",
                                        "mean": 21.5,
                                        "min": 20.0,
                                        "max": 23.0,
                                        "stddev": 1.0,
                                    }
                                },
                            }
                        },
                    }
                ]
            }
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("future_sensor/future.metrics/temperature", lines[0])
        self.assertIn("mean=21.50", lines[0])
        self.assertIn("drops=2", lines[0])

    def test_canonical_biosignal_format_uses_only_card_summary(self) -> None:
        lines = adapter._format_biosignals(
            {"brainbit": {"enabled": True}},
            {
                "card_summary": {
                    "schema": "study-runner/card-summary/v1",
                    "cards": [
                        {
                            "question_index": 0,
                            "streams": {
                                "brainbit.metrics": {
                                    "plugin_key": "brainbit",
                                    "count": 2,
                                    "valid_count": 2,
                                    "coverage": 1.0,
                                    "missing_count": 0,
                                    "drop_count": 0,
                                    "max_gap_seconds": 0.1,
                                    "channels": {
                                        "attention": {
                                            "kind": "numeric",
                                            "mean": 0.25,
                                            "min": 0.2,
                                            "max": 0.3,
                                            "stddev": 0.07,
                                        }
                                    },
                                }
                            },
                        }
                    ],
                },
                "biosignal_summary": {"brainbit": {"active": True, "mean": 98765}},
            },
            canonical_output=True,
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("mean=0.25", lines[0])
        self.assertNotIn("98765", lines[0])

    def test_canonical_notion_payload_requires_valid_card_summary(self) -> None:
        result = adapter.upload_study_result(
            result_payload={
                "session_id": "session-1",
                "server_finalization": {"card_summary_file": "card-summary.json"},
            },
            hardware_config={},
            saved_output={"card_summary_file": "sessions/session-1/card-summary.json"},
            config_data={"study_settings": {"notion_enabled": True}},
        )

        self.assertFalse(result["ok"])
        self.assertIn("requires finalized card-summary.json", result["error"])

    def test_canonical_notion_session_block_never_renders_embedded_ram_summary(self) -> None:
        class Children:
            def __init__(self) -> None:
                self.calls = []

            def append(self, **kwargs):
                self.calls.append(kwargs)
                return {"results": [{"id": "toggle-1"}]}

        class Client:
            def __init__(self) -> None:
                self.blocks = type("Blocks", (), {"children": Children()})()

        client = Client()
        adapter._append_session_block(
            client,
            "participant-page",
            1,
            {
                "study_id": "Study",
                "session_id": "session-1",
                "timestamp_start": "2026-01-01T10:00:00Z",
                "timestamp_end": "2026-01-01T10:01:00Z",
                "server_finalization": {"card_summary_file": "card-summary.json"},
                "answer_details": [
                    {
                        "question_number": 1,
                        "question_type": "likert",
                        "answer": 4,
                        "biosignal_interval": {
                            "brainbit": {"available": True, "avg_attention": 12345.0}
                        },
                    }
                ],
            },
            {"brainbit": {"enabled": True}},
            {
                "card_summary_file": "sessions/session-1/card-summary.json",
                "card_summary": {
                    "schema": "study-runner/card-summary/v1",
                    "cards": [
                        {
                            "question_index": 0,
                            "streams": {
                                "brainbit.metrics": {
                                    "plugin_key": "brainbit",
                                    "count": 1,
                                    "valid_count": 1,
                                    "coverage": 1.0,
                                    "missing_count": 0,
                                    "drop_count": 0,
                                    "max_gap_seconds": 0.0,
                                    "channels": {
                                        "attention": {
                                            "kind": "numeric",
                                            "mean": 0.25,
                                            "min": 0.25,
                                            "max": 0.25,
                                            "stddev": None,
                                        }
                                    },
                                }
                            },
                        }
                    ],
                },
                "biosignal_summary": {"brainbit": {"active": True, "mean": 98765}},
            },
        )

        rendered = json.dumps(client.blocks.children.calls, ensure_ascii=False)
        self.assertIn("mean=0.25", rendered)
        self.assertNotIn("12345", rendered)
        self.assertNotIn("98765", rendered)


if __name__ == "__main__":
    unittest.main()
