"""Small, shared input primitives; card semantics live in their extensions."""
from __future__ import annotations

from typing import Any


class CardValidationError(ValueError):
    """Invalid study input, distinct from a failed extension process."""


def normalize_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def require_text(value: Any, field_name: str) -> str:
    normalized = normalize_text(value)
    if not normalized:
        raise CardValidationError(f"{field_name} is required.")
    return normalized


def normalize_integer(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise CardValidationError(f"{field_name} must be a whole number.") from exc

    if normalized < minimum or normalized > maximum:
        raise CardValidationError(f"{field_name} must be between {minimum} and {maximum}.")
    return normalized


def normalize_optional_integer(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if value in (None, ""):
        return None
    return normalize_integer(value, field_name=field_name, minimum=minimum, maximum=maximum)


def normalize_float(
    value: Any,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
    allow_none: bool = False,
) -> float | None:
    if value in (None, ""):
        if allow_none:
            return None
        raise CardValidationError(f"{field_name} is required.")

    if isinstance(value, bool):
        raise CardValidationError(f"{field_name} must be a number.")

    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise CardValidationError(f"{field_name} must be a number.") from exc

    if normalized < minimum or normalized > maximum:
        raise CardValidationError(f"{field_name} must be between {minimum} and {maximum}.")
    return normalized


def normalize_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CardValidationError("Options must be a list of text entries.")
    return [entry for entry in (normalize_text(item) for item in value) if entry]
