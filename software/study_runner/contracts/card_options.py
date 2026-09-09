"""Options-list normalization shared by choice and ranking cards."""
from __future__ import annotations

from typing import Any
from .card_validation_primitives import (
    CardValidationError,
    normalize_text,
    normalize_text_list,
)


def normalize_options_question(
    question_data: dict[str, Any], question_index: int, *, question_type: str
) -> dict[str, Any]:
    """Shared by choice/single/ranking: an options list, nothing else."""
    options = normalize_text_list(question_data.get("options"))
    if not options:
        raise CardValidationError(f"Question {question_index} needs at least one option.")
    return {
        "type": question_type,
        "prompt": normalize_text(question_data.get("prompt")),
        "options": options,
    }
