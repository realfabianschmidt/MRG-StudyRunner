"""The Notion export mapping: flexible targets, column sources, and reducers.

An ``export_mapping`` study setting replaces the plugin's old hardcoded
two-database shape with an operator-defined list of *targets*. Each target is
one Notion database with a declared **row level** - one row per session, per
participant, or per card - and this module decides what a source id means at
that level, what a reducer does, and what Notion property values a mapping
produces for one session. It never calls the Notion API; `adapter.py` owns
that and calls into this module for the computation.

Source id grammar (see `build_output_catalog`):
  - ``session.<field>``                               always one value.
  - ``participant.<field>``                            always one value.
  - ``card.<field>``                                   one value per card.
  - ``card[<index>].<field>``                           one card, picked by index.
  - ``card.stream.<key>.channel.<name>.<stat>``          one value per card.
  - ``card[<index>].stream.<key>.channel.<name>.<stat>`` one card, picked by index.

A session- or participant-level target sees every ``card.*`` source as a
*list* (one entry per card) and must reduce it to one value with a
``reducer``. A card-level target sees the same source as the single value of
the row's own card and must not declare a reducer - that is the cardinality
rule item/list plays in a node editor, applied to column mapping instead.
"""
from __future__ import annotations

import re
from typing import Any

from study_runner.contracts.participant_fields import PARTICIPANT_FIELD_ORDER

ROW_LEVELS = ("session", "participant", "card")
REDUCERS = ("mean", "min", "max", "count", "join", "first", "last")
COLUMN_TYPES = ("rich_text", "number", "date", "select", "multi_select")
PRESETS = ("as_before", "simple", "analysis", "custom")

# The row level fixes the key column; it is never one of the mapped columns.
KEY_COLUMN_NAME = {
    "session": "Session ID",
    "participant": "Participant ID",
    "card": "Row Key",
}

DEFAULT_EXPORT_MAPPING: dict[str, Any] = {"preset": "as_before", "targets": []}

_CARD_SOURCE = re.compile(
    r"^card(?:\[(?P<index>\d+)\])?\.(?P<field>prompt|answer|duration_seconds|skipped)$"
)
_CARD_STREAM_SOURCE = re.compile(
    r"^card(?:\[(?P<index>\d+)\])?\.stream\.(?P<stream>[^.]+)\.channel\.(?P<channel>[^.]+)\.(?P<stat>mean|min|max|stddev|mode|coverage|max_gap_seconds)$"
)
_SESSION_FIELDS = {
    "session_id", "participant_id", "study_id", "start", "end",
    "duration_minutes", "card_count", "answered_count", "skipped_count",
}


class MappingError(ValueError):
    """An export mapping (or one target/column within it) is invalid."""


# ── Validation ────────────────────────────────────────────────────────────

def validate_export_mapping(value: Any, *, config_data: dict[str, Any] | None = None) -> None:
    """Raise MappingError for anything the evaluator could not safely run.

    Called both by the study-settings validator (every save) and by the
    configurator before it offers to create a database, so an invalid
    mapping is caught before it reaches an upload.
    """
    if not isinstance(value, dict):
        raise MappingError("export_mapping must be a JSON object.")
    extra = set(value) - {"preset", "targets"}
    if extra:
        raise MappingError(f"export_mapping has unsupported fields: {', '.join(sorted(extra))}.")
    preset = value.get("preset", "as_before")
    if preset not in PRESETS:
        raise MappingError(f"export_mapping.preset must be one of: {', '.join(PRESETS)}.")
    targets = value.get("targets", [])
    if not isinstance(targets, list):
        raise MappingError("export_mapping.targets must be a list.")
    participant_fields = set(_participant_fields(config_data)) if config_data else None
    seen_ids: set[str] = set()
    for index, target in enumerate(targets):
        path = f"export_mapping.targets[{index}]"
        if not isinstance(target, dict):
            raise MappingError(f"{path} must be a JSON object.")
        target_id = str(target.get("id") or "").strip()
        if not target_id:
            raise MappingError(f"{path}.id is required.")
        if target_id in seen_ids:
            raise MappingError(f"export_mapping.targets contains duplicate id {target_id!r}.")
        seen_ids.add(target_id)
        if not str(target.get("title") or "").strip():
            raise MappingError(f"{path}.title is required.")
        row_level = target.get("row_level")
        if row_level not in ROW_LEVELS:
            raise MappingError(f"{path}.row_level must be one of: {', '.join(ROW_LEVELS)}.")
        columns = target.get("columns")
        if not isinstance(columns, dict) or not columns:
            raise MappingError(f"{path}.columns must be a non-empty JSON object.")
        for column_name, column in columns.items():
            if not isinstance(column_name, str) or not column_name.strip():
                raise MappingError(f"{path}.columns has an empty column name.")
            _validate_column(f"{path}.columns.{column_name}", column, row_level, participant_fields)
        relations = target.get("relations", [])
        if relations and not isinstance(relations, list):
            raise MappingError(f"{path}.relations must be a list.")
        for relation_index, relation in enumerate(relations or []):
            if not isinstance(relation, dict) or not str(relation.get("target_id") or "").strip():
                raise MappingError(f"{path}.relations[{relation_index}].target_id is required.")
    for target in targets:
        for relation in (target.get("relations") or []) if isinstance(target, dict) else []:
            if isinstance(relation, dict) and relation.get("target_id") not in seen_ids:
                raise MappingError(
                    f"export_mapping.targets[{target.get('id')}] relates to an unknown target."
                )


def _validate_column(
    path: str,
    column: Any,
    row_level: str,
    participant_fields: set[str] | None,
) -> None:
    if not isinstance(column, dict):
        raise MappingError(f"{path} must be a JSON object.")
    extra = set(column) - {"type", "source", "reducer"}
    if extra:
        raise MappingError(f"{path} has unsupported fields: {', '.join(sorted(extra))}.")
    column_type = column.get("type")
    if column_type not in COLUMN_TYPES:
        raise MappingError(f"{path}.type must be one of: {', '.join(COLUMN_TYPES)}.")
    source = str(column.get("source") or "").strip()
    if not source:
        raise MappingError(f"{path}.source is required.")
    level, indexed = _source_level(source, participant_fields)
    if level is None:
        raise MappingError(f"{path}.source {source!r} is not a recognized output.")
    reducer = column.get("reducer")
    needs_reducer = level == "card" and row_level != "card" and not indexed
    if needs_reducer and not reducer:
        raise MappingError(
            f"{path}.source {source!r} is a value per card; add a reducer "
            "(mean/min/max/count/join/first/last) or pick one card with card[<index>]."
        )
    if reducer is not None:
        if reducer not in REDUCERS:
            raise MappingError(f"{path}.reducer must be one of: {', '.join(REDUCERS)}.")
        if not needs_reducer:
            raise MappingError(
                f"{path}.reducer is set but {source!r} is already a single value here; remove it."
            )


def _source_level(source: str, participant_fields: set[str] | None) -> tuple[str | None, bool]:
    """(level, indexed) for a source id, or (None, False) if unrecognized."""
    if source.startswith("session."):
        field = source.split(".", 1)[1]
        return ("session", False) if field in _SESSION_FIELDS else (None, False)
    if source.startswith("participant."):
        field = source.split(".", 1)[1]
        if participant_fields is None or field in participant_fields:
            return ("participant", False)
        return (None, False)
    match = _CARD_SOURCE.match(source) or _CARD_STREAM_SOURCE.match(source)
    if match:
        return ("card", match.group("index") is not None)
    return (None, False)


def _participant_fields(config_data: dict[str, Any]) -> list[str]:
    questions = config_data.get("questions") or []
    participant_question = next(
        (q for q in questions if isinstance(q, dict) and q.get("type") == "participant-id"),
        None,
    )
    if not participant_question:
        return ["participant_id"]
    fields = participant_question.get("fields") or {}
    stored = [key for key in PARTICIPANT_FIELD_ORDER if (fields.get(key) or {}).get("store")]
    return ["participant_id", *stored]


# ── Output catalog (what a column can be mapped from) ──────────────────────

def build_output_catalog(config_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every source id a column could map, with a human label and a type."""
    catalog: list[dict[str, Any]] = [
        {"source": "session.session_id", "label": "Session ID", "value_type": "string"},
        {"source": "session.participant_id", "label": "Participant ID", "value_type": "string"},
        {"source": "session.study_id", "label": "Study", "value_type": "string"},
        {"source": "session.start", "label": "Session start", "value_type": "date"},
        {"source": "session.end", "label": "Session end", "value_type": "date"},
        {"source": "session.duration_minutes", "label": "Duration (min)", "value_type": "number"},
        {"source": "session.card_count", "label": "Card count", "value_type": "number"},
        {"source": "session.answered_count", "label": "Answered count", "value_type": "number"},
        {"source": "session.skipped_count", "label": "Skipped count", "value_type": "number"},
    ]
    for field in _participant_fields(config_data):
        if field == "participant_id":
            continue
        catalog.append({"source": f"participant.{field}", "label": f"Participant: {field}", "value_type": "string"})
    catalog.append({"source": "card.prompt", "label": "Card: prompt", "value_type": "string", "per_card": True})
    catalog.append({"source": "card.answer", "label": "Card: answer", "value_type": "string", "per_card": True})
    catalog.append({"source": "card.duration_seconds", "label": "Card: duration (s)", "value_type": "number", "per_card": True})
    catalog.append({"source": "card.skipped", "label": "Card: skipped", "value_type": "string", "per_card": True})
    return catalog


# ── Evaluation ───────────────────────────────────────────────────────────

def session_context(
    result_payload: dict[str, Any],
    card_summary: dict[str, Any] | None,
    config_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One session's data, shaped for `evaluate_target` - no Notion client needed."""
    details = [d for d in (result_payload.get("answer_details") or []) if isinstance(d, dict)]
    answerable = [d for d in details if d.get("question_type") not in {"stimulus", "info", "finish", "participant-id"}]
    skipped = sum(1 for d in answerable if d.get("skipped"))
    cards_by_index: dict[int, dict[str, Any]] = {}
    for summary_card in ((card_summary or {}).get("cards") or []):
        if isinstance(summary_card, dict) and isinstance(summary_card.get("question_index"), int):
            cards_by_index[summary_card["question_index"]] = summary_card
    cards: list[dict[str, Any]] = []
    for detail in details:
        index = detail.get("question_index")
        cards.append({
            "prompt": str(detail.get("question_prompt") or ""),
            "answer": detail.get("answer"),
            "duration_seconds": detail.get("interval_seconds", detail.get("seconds_since_previous_answer")),
            "skipped": bool(detail.get("skipped")),
            "streams": (cards_by_index.get(index) or {}).get("streams") or {},
        })
    ts_start = str(result_payload.get("timestamp_start") or "")
    ts_end = str(result_payload.get("timestamp_end") or "")
    return {
        "session": {
            "session_id": str(result_payload.get("session_id") or ""),
            "participant_id": str(result_payload.get("participant_id") or ""),
            "study_id": str(result_payload.get("study_id") or ""),
            "start": ts_start,
            "end": ts_end,
            "duration_minutes": _minutes_between(ts_start, ts_end),
            "card_count": len(details),
            "answered_count": len(answerable) - skipped,
            "skipped_count": skipped,
        },
        "participant": dict((result_payload.get("participant_metadata") or {}) if isinstance(result_payload.get("participant_metadata"), dict) else {}),
        "cards": cards,
    }


def evaluate_target(target: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    """Notion-ready property values for one target: one row, or one per card."""
    row_level = target["row_level"]
    rows: list[dict[str, Any]] = []
    card_indices = range(len(context["cards"])) if row_level == "card" else [None]
    for card_index in card_indices:
        properties: dict[str, Any] = {}
        for column_name, column in target["columns"].items():
            value = _resolve(column["source"], context, current_card=card_index)
            if isinstance(value, list) and column.get("reducer"):
                value = _reduce(value, column["reducer"])
            properties[column_name] = _coerce(value, column["type"])
        rows.append({"card_index": card_index, "properties": properties})
    return rows


def key_value(target: dict[str, Any], context: dict[str, Any], card_index: int | None) -> str:
    row_level = target["row_level"]
    if row_level == "session":
        return context["session"]["session_id"]
    if row_level == "participant":
        return context["session"]["participant_id"]
    return f"{context['session']['session_id']}:{card_index}"


def _resolve(source: str, context: dict[str, Any], *, current_card: int | None) -> Any:
    if source.startswith("session."):
        return context["session"].get(source.split(".", 1)[1])
    if source.startswith("participant."):
        return context["participant"].get(source.split(".", 1)[1])
    match = _CARD_SOURCE.match(source)
    if match:
        return _resolve_card_field(context, match, current_card, lambda card: card.get(match.group("field")))
    match = _CARD_STREAM_SOURCE.match(source)
    if match:
        def read(card: dict[str, Any]) -> Any:
            stream = (card.get("streams") or {}).get(match.group("stream")) or {}
            channel = (stream.get("channels") or {}).get(match.group("channel")) or {}
            return channel.get(match.group("stat"))
        return _resolve_card_field(context, match, current_card, read)
    return None


def _resolve_card_field(context: dict[str, Any], match: re.Match, current_card: int | None, read) -> Any:
    index_text = match.group("index")
    if index_text is not None:
        cards = context["cards"]
        index = int(index_text)
        return read(cards[index]) if 0 <= index < len(cards) else None
    if current_card is not None:
        return read(context["cards"][current_card])
    return [read(card) for card in context["cards"]]


def _reduce(values: list[Any], reducer: str) -> Any:
    numeric = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if reducer == "count":
        return sum(1 for v in values if v not in (None, ""))
    if reducer == "mean":
        return sum(numeric) / len(numeric) if numeric else None
    if reducer == "min":
        return min(numeric) if numeric else None
    if reducer == "max":
        return max(numeric) if numeric else None
    if reducer == "first":
        return values[0] if values else None
    if reducer == "last":
        return values[-1] if values else None
    if reducer == "join":
        return ", ".join(str(v) for v in values if v not in (None, ""))
    return None


def _coerce(value: Any, column_type: str) -> Any:
    if value is None:
        return None
    if column_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if column_type in {"select", "multi_select"}:
        if isinstance(value, list):
            return [str(v) for v in value if v not in (None, "")]
        return str(value)
    return str(value)


def _minutes_between(start: str, end: str) -> float | None:
    try:
        import datetime

        t0 = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
        return round((t1 - t0).total_seconds() / 60, 2)
    except (TypeError, ValueError):
        return None
