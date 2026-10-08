"""The Notion export mapping's evaluator: pure logic, no Notion client.

See docs/notion-plugin-configurator-plan.md, Phase 2. This is the part of
the configurator that decides what a mapped column actually computes; the
admin actions that browse/create Notion databases are covered separately in
test_notion_upload.py with a mocked client.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from study_runner.plugins.destinations.notion_upload.mapping import (
    DEFAULT_EXPORT_MAPPING,
    KEY_COLUMN_NAME,
    MappingError,
    build_output_catalog,
    evaluate_target,
    key_column_for,
    key_value,
    session_context,
    validate_export_mapping,
)


RESULT_PAYLOAD = {
    "session_id": "session-1",
    "participant_id": "participant-1",
    "study_id": "study-a",
    "timestamp_start": "2026-01-01T10:00:00Z",
    "timestamp_end": "2026-01-01T10:05:00Z",
    "answer_details": [
        {"question_index": 0, "question_prompt": "How are you?", "answer": "fine", "question_type": "text", "interval_seconds": 5.0},
        {"question_index": 1, "question_prompt": "Rate it", "answer": 3, "question_type": "likert", "interval_seconds": 3.0, "skipped": False},
        {"question_index": 2, "question_prompt": "Skipped one", "answer": None, "question_type": "text", "skipped": True},
    ],
}
CARD_SUMMARY = {
    "schema": "study-runner/card-summary/v1",
    "cards": [
        {"question_index": 0, "streams": {"brainbit": {"channels": {"alpha": {"mean": 1.0}}}}},
        {"question_index": 1, "streams": {"brainbit": {"channels": {"alpha": {"mean": 3.0}}}}},
        {"question_index": 2, "streams": {}},
    ],
}


def session_target(**columns: dict) -> dict:
    return {"id": "t1", "title": "Sessions", "row_level": "session", "database_id": "db1", "columns": columns}


class ExportMappingValidationTests(unittest.TestCase):
    def test_default_mapping_is_the_as_before_preset_and_always_valid(self) -> None:
        self.assertEqual(DEFAULT_EXPORT_MAPPING, {"preset": "as_before", "targets": []})
        validate_export_mapping(DEFAULT_EXPORT_MAPPING)

    def test_a_per_card_source_without_a_reducer_is_rejected_on_a_session_target(self) -> None:
        mapping = {"preset": "custom", "targets": [session_target(
            Alpha={"type": "number", "source": "card.stream.brainbit.channel.alpha.mean"},
        )]}
        with self.assertRaisesRegex(MappingError, "add a reducer"):
            validate_export_mapping(mapping)

    def test_the_same_source_with_a_reducer_is_accepted(self) -> None:
        mapping = {"preset": "custom", "targets": [session_target(
            Alpha={"type": "number", "source": "card.stream.brainbit.channel.alpha.mean", "reducer": "mean"},
        )]}
        validate_export_mapping(mapping)  # does not raise

    def test_an_indexed_card_source_needs_no_reducer(self) -> None:
        mapping = {"preset": "custom", "targets": [session_target(
            Q1={"type": "rich_text", "source": "card[0].answer"},
        )]}
        validate_export_mapping(mapping)  # does not raise

    def test_a_reducer_on_an_already_scalar_source_is_rejected(self) -> None:
        mapping = {"preset": "custom", "targets": [session_target(
            Pid={"type": "rich_text", "source": "session.participant_id", "reducer": "join"},
        )]}
        with self.assertRaisesRegex(MappingError, "already a single value"):
            validate_export_mapping(mapping)

    def test_a_card_level_target_rejects_a_reducer_on_its_own_card_field(self) -> None:
        mapping = {"preset": "custom", "targets": [{
            "id": "t2", "title": "Answers", "row_level": "card", "database_id": "db2",
            "columns": {"Alpha": {"type": "number", "source": "card.stream.brainbit.channel.alpha.mean", "reducer": "mean"}},
        }]}
        with self.assertRaisesRegex(MappingError, "already a single value"):
            validate_export_mapping(mapping)

    def test_duplicate_target_ids_are_rejected(self) -> None:
        target = session_target(Pid={"type": "rich_text", "source": "session.participant_id"})
        mapping = {"preset": "custom", "targets": [target, dict(target)]}
        with self.assertRaisesRegex(MappingError, "duplicate id"):
            validate_export_mapping(mapping)

    def test_an_unrecognized_source_is_rejected(self) -> None:
        mapping = {"preset": "custom", "targets": [session_target(
            Bad={"type": "rich_text", "source": "not.a.real.source"},
        )]}
        with self.assertRaisesRegex(MappingError, "not a recognized output"):
            validate_export_mapping(mapping)

    def test_an_unknown_participant_field_is_rejected_against_the_studys_own_catalog(self) -> None:
        config_data = {"questions": [{"type": "participant-id", "fields": {"age_group": {"store": True}}}]}
        mapping = {"preset": "custom", "targets": [session_target(
            City={"type": "rich_text", "source": "participant.childhood_nearest_city"},
        )]}
        with self.assertRaisesRegex(MappingError, "not a recognized output"):
            validate_export_mapping(mapping, config_data=config_data)

    def test_a_custom_key_column_name_is_accepted_and_used(self) -> None:
        target = session_target(Pid={"type": "rich_text", "source": "session.participant_id"})
        target["key_column"] = "Sitzungs-ID"
        validate_export_mapping({"preset": "custom", "targets": [target]})
        self.assertEqual(key_column_for(target), "Sitzungs-ID")
        self.assertEqual(key_column_for(session_target()), "Session ID")

    def test_an_empty_or_overlong_key_column_is_rejected(self) -> None:
        for bad in ("", "   ", "x" * 101, 42):
            with self.subTest(bad=bad):
                target = session_target(Pid={"type": "rich_text", "source": "session.participant_id"})
                target["key_column"] = bad
                with self.assertRaisesRegex(MappingError, "key_column"):
                    validate_export_mapping({"preset": "custom", "targets": [target]})

    def test_a_column_repeating_the_key_column_is_rejected(self) -> None:
        target = session_target(**{"Session ID": {"type": "rich_text", "source": "session.session_id"}})
        with self.assertRaisesRegex(MappingError, "must not repeat the key column"):
            validate_export_mapping({"preset": "custom", "targets": [target]})

    def test_invalid_row_level_is_rejected(self) -> None:
        mapping = {"preset": "custom", "targets": [{
            "id": "t1", "title": "X", "row_level": "study", "database_id": "",
            "columns": {"A": {"type": "rich_text", "source": "session.session_id"}},
        }]}
        with self.assertRaisesRegex(MappingError, "row_level"):
            validate_export_mapping(mapping)

    def test_a_relation_to_an_unknown_target_is_rejected(self) -> None:
        target = session_target(Pid={"type": "rich_text", "source": "session.participant_id"})
        target["relations"] = [{"target_id": "does-not-exist"}]
        with self.assertRaisesRegex(MappingError, "unknown target"):
            validate_export_mapping({"preset": "custom", "targets": [target]})


class ExportMappingEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = session_context(RESULT_PAYLOAD, CARD_SUMMARY)

    def test_session_level_counts_and_duration(self) -> None:
        self.assertEqual(self.context["session"]["card_count"], 3)
        self.assertEqual(self.context["session"]["skipped_count"], 1)
        self.assertEqual(self.context["session"]["answered_count"], 2)
        self.assertEqual(self.context["session"]["duration_minutes"], 5.0)

    def test_a_reducer_averages_across_every_card(self) -> None:
        target = session_target(Alpha={"type": "number", "source": "card.stream.brainbit.channel.alpha.mean", "reducer": "mean"})
        [row] = evaluate_target(target, self.context)
        self.assertEqual(row["properties"]["Alpha"], 2.0)  # mean of 1.0 and 3.0; card 2 has none

    def test_count_reducer_counts_non_empty_values(self) -> None:
        target = session_target(Answered={"type": "number", "source": "card.answer", "reducer": "count"})
        [row] = evaluate_target(target, self.context)
        self.assertEqual(row["properties"]["Answered"], 2)  # "fine" and 3; the skipped card has None

    def test_join_reducer_concatenates_text_values(self) -> None:
        target = session_target(Prompts={"type": "rich_text", "source": "card.prompt", "reducer": "join"})
        [row] = evaluate_target(target, self.context)
        self.assertEqual(row["properties"]["Prompts"], "How are you?, Rate it, Skipped one")

    def test_an_indexed_card_source_picks_that_one_card(self) -> None:
        target = session_target(Q1={"type": "rich_text", "source": "card[1].answer"})
        [row] = evaluate_target(target, self.context)
        self.assertEqual(row["properties"]["Q1"], "3")

    def test_an_out_of_range_card_index_resolves_to_none(self) -> None:
        target = session_target(Q9={"type": "rich_text", "source": "card[99].answer"})
        [row] = evaluate_target(target, self.context)
        self.assertIsNone(row["properties"]["Q9"])

    def test_a_card_level_target_produces_one_row_per_card(self) -> None:
        target = {
            "id": "t2", "title": "Answers", "row_level": "card", "database_id": "db2",
            "columns": {
                "Prompt": {"type": "rich_text", "source": "card.prompt"},
                "Alpha": {"type": "number", "source": "card.stream.brainbit.channel.alpha.mean"},
            },
        }
        rows = evaluate_target(target, self.context)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["properties"]["Prompt"], "How are you?")
        self.assertEqual(rows[0]["properties"]["Alpha"], 1.0)
        self.assertIsNone(rows[2]["properties"]["Alpha"])

    def test_key_value_matches_the_row_level(self) -> None:
        session_tgt = session_target(Pid={"type": "rich_text", "source": "session.participant_id"})
        self.assertEqual(key_value(session_tgt, self.context, None), "session-1")
        participant_tgt = {**session_tgt, "row_level": "participant"}
        self.assertEqual(key_value(participant_tgt, self.context, None), "participant-1")
        card_tgt = {**session_tgt, "row_level": "card"}
        self.assertEqual(key_value(card_tgt, self.context, 2), "session-1:2")

    def test_key_column_name_covers_every_row_level(self) -> None:
        self.assertEqual(set(KEY_COLUMN_NAME), {"session", "participant", "card"})


class OutputCatalogTests(unittest.TestCase):
    def test_catalog_includes_core_session_fields_and_stored_participant_fields(self) -> None:
        config_data = {"questions": [{"type": "participant-id", "fields": {
            "age_group": {"store": True}, "first_name": {"store": False},
        }}]}
        sources = {entry["source"] for entry in build_output_catalog(config_data)}
        self.assertIn("session.participant_id", sources)
        self.assertIn("session.duration_minutes", sources)
        self.assertIn("participant.age_group", sources)
        self.assertNotIn("participant.first_name", sources)
        self.assertIn("card.answer", sources)


if __name__ == "__main__":
    unittest.main()
