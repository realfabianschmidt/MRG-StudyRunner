from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.runtime_core.studies.validation import validate_and_normalize_config
from study_runner.plugins.destinations.notion_upload import adapter, plugin


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

    def test_answer_table_lists_every_card_with_answer_and_duration(self) -> None:
        rows = adapter._answer_table_rows(
            {
                "answer_details": [
                    {"question_number": 1, "question_type": "stimulus", "question_prompt": "Observe", "answer": "stimulus",
                     "interval_seconds": 7.0},
                    {"question_number": 2, "question_type": "mood-meter", "question_prompt": "How do you feel?",
                     "answer": ["Calm", "Content"], "shown_at": "2026-01-01T10:00:00Z", "answered_at": "2026-01-01T10:00:04Z"},
                    {"question_number": 3, "question_type": "likert", "question_prompt": "Optional", "answer": None,
                     "skipped": True},
                ]
            }
        )

        self.assertEqual(rows[0], ["1", "stimulus", "Observe", "(Stimulus)", "7.0"])
        self.assertEqual(rows[1], ["2", "mood-meter", "How do you feel?", "Calm, Content", "4.0"])
        self.assertEqual(rows[2][3], "\u2014 (übersprungen)")

    def test_canonical_answers_never_include_embedded_ram_biomarkers(self) -> None:
        rows = adapter._answer_table_rows(
            {"answer_details": [{"question_number": 1, "question_type": "likert", "answer": 4,
                                 "biosignal_interval": {"brainbit": {"available": True, "avg_attention": 12345.0}}}]}
        )
        self.assertNotIn("12345", json.dumps(rows))

    def test_biosignal_table_renders_unknown_plugins_per_card_without_core_changes(self) -> None:
        rows = adapter._biosignal_table_rows(
            {
            "schema": "study-runner/card-summary/v1",
            "cards": [
                {
                    "question_index": 1,
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
                                "temperature": {"kind": "numeric", "mean": 21.5, "min": 20.0, "max": 23.0, "stddev": 1.0},
                                "state": {"kind": "categorical", "mode": "calm", "frequencies": {"calm": 9}},
                            },
                        }
                    },
                }
            ],
        },
            {"answer_details": [{"question_index": 1, "question_number": 2, "question_type": "mood-meter"}]},
        )

        self.assertEqual(
            rows[0],
            ["Q2 · mood-meter", "future_sensor / future.metrics", "temperature", "21.50", "20.00", "23.00", "1.00", "90 %", "0.30"],
        )
        self.assertEqual(rows[1][2:4], ["state", "calm"])

    def test_tables_split_at_the_notion_row_limit(self) -> None:
        tables = adapter._tables(["a"], [[str(n)] for n in range(150)])
        self.assertEqual(len(tables), 2)
        self.assertEqual(len(tables[0]["table"]["children"]), 100)
        self.assertEqual(tables[0]["table"]["children"][0]["table_row"]["cells"][0][0]["text"]["content"], "a")

    def test_session_page_uses_only_the_canonical_card_summary(self) -> None:
        blocks = adapter._session_page_blocks(
            {"study_id": "Study", "timestamp_start": "2026-01-01T10:00:00Z", "timestamp_end": "2026-01-01T10:01:00Z",
             "answer_details": [{"question_index": 1, "question_number": 2, "question_type": "likert", "answer": 4}]},
            {"brainbit": {"enabled": True}},
            {"card_summary": {
            "schema": "study-runner/card-summary/v1",
            "cards": [
                {
                    "question_index": 1,
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
                                "temperature": {"kind": "numeric", "mean": 21.5, "min": 20.0, "max": 23.0, "stddev": 1.0},
                                "state": {"kind": "categorical", "mode": "calm", "frequencies": {"calm": 9}},
                            },
                        }
                    },
                }
            ],
        }, "biosignal_summary": {"brainbit": {"active": True, "mean": 98765}}},
        )
        rendered = json.dumps(blocks, ensure_ascii=False)
        self.assertEqual([b["type"] for b in blocks].count("table"), 2)
        self.assertIn("21.50", rendered)
        self.assertNotIn("98765", rendered)

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


class NotionPublishContractTests(unittest.TestCase):
    def payload(self):
        return {
            "config_data": {"study_id": "Study A", "study_settings": {"plugins": {
                "notion": {"enabled": True, "settings": {"parent_page_id": "parent-1"}},
            }}},
            "result_payload": {"session_id": "session-1", "participant_id": "p1", "answers": {}},
            "saved_output": {},
        }

    def client(self, existing_children=None):
        created = iter([
            {"id": "created-db", "data_sources": [{"id": "source-1"}]},
            {"id": "sessions-db", "data_sources": [{"id": "sessions-source"}]},
        ])
        sources = {"createddb": "source-1", "sessionsdb": "sessions-source"}
        return SimpleNamespace(
            databases=SimpleNamespace(
                create=Mock(side_effect=lambda **_: next(created)),
                retrieve=Mock(side_effect=lambda database_id: {"data_sources": [{"id": sources.get(database_id, database_id)}]}),
            ),
            data_sources=SimpleNamespace(query=Mock(return_value={"results": []})),
            pages=SimpleNamespace(
                create=Mock(side_effect=[{"id": "participant-page"}, {"id": "session-page"}]),
                retrieve=Mock(return_value={"properties": {}}), update=Mock(),
            ),
            blocks=SimpleNamespace(children=SimpleNamespace(
                list=Mock(return_value={"results": existing_children or [], "has_more": False}),
                append=Mock(return_value={"results": []}),
            )),
        )

    def test_real_publish_adapter_participant_path_reports_created_target(self):
        payload = self.payload()
        original = deepcopy(payload)
        client = self.client()
        with patch.object(adapter, "get_client", return_value=client):
            result = plugin._publish(SimpleNamespace(hardware_config={}, secret=lambda *_: "study-key"), payload)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["upsert"], "created")
        self.assertEqual(
            result["study_config_updates"],
            {"database_id": "createddb", "data_source_id": "source-1", "sessions_database_id": "sessionsdb"},
        )
        participant_query, session_query = client.data_sources.query.call_args_list
        self.assertEqual(participant_query.kwargs["data_source_id"], "source-1")
        self.assertEqual(session_query.kwargs["data_source_id"], "sessions-source")
        session_row = client.pages.create.call_args_list[1].kwargs
        self.assertEqual(session_row["properties"]["Participant"], {"relation": [{"id": "participant-page"}]})
        self.assertEqual(client.blocks.children.append.call_args.kwargs["block_id"], "session-page")
        self.assertEqual(payload, original)

    def test_existing_databases_under_the_parent_page_are_reused_not_duplicated(self):
        children = [
            {"type": "child_database", "id": "existing-participants", "child_database": {"title": "StudyRunner Participants"}},
            {"type": "child_database", "id": "existing-sessions", "child_database": {"title": "StudyRunner Sessions"}},
        ]
        client = self.client(existing_children=children)
        with patch.object(adapter, "get_client", return_value=client):
            result = plugin._publish(SimpleNamespace(hardware_config={}, secret=lambda *_: "study-key"), self.payload())
        self.assertTrue(result["ok"], result)
        client.databases.create.assert_not_called()
        self.assertEqual(result["study_config_updates"]["database_id"], "existingparticipants")
        self.assertEqual(result["study_config_updates"]["sessions_database_id"], "existingsessions")

    def test_failure_after_target_creation_returns_discoveries_for_retry(self):
        client = self.client()
        client.pages.create.side_effect = OSError("connection interrupted")
        with patch.object(adapter, "get_client", return_value=client):
            result = plugin._publish(SimpleNamespace(hardware_config={}, secret=lambda *_: "study-key"), self.payload())
        self.assertFalse(result["ok"])
        self.assertEqual(result["study_config_updates"], {"database_id": "createddb", "data_source_id": "source-1"})
        self.assertIn("connection interrupted", result["error"])


if __name__ == "__main__":
    unittest.main()
