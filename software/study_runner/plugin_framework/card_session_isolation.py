"""Card modules must not keep participant data between sessions.

A card's browser module lives as long as the participant page, so anything it
stores in a module-level variable would reach the next participant. The rule
for every card is therefore: no mutable module-level state. A card either
reads its answer back from the rendered DOM or keeps it through
``/static/scripts/cards/session-state.js`` (``cardState``), which the
participant page clears at every session boundary.

This check enforces the rule when a card plugin is discovered, so a card that
breaks it is refused instead of silently leaking data. It is a line-based
scan of top-level declarations (column 0), which is how every card module is
written.
"""
from __future__ import annotations

import re
from pathlib import Path

# Set exactly once from the study's card defaults by configureCard(); it holds
# configuration, never anything a participant entered.
ALLOWED_TOP_LEVEL_LETS = frozenset({"defaultQuestion"})

_TOP_LEVEL_LET = re.compile(r"^(?:export\s+)?(?:let|var)\s+([A-Za-z_$][\w$]*)")
_TOP_LEVEL_EMPTY_CONTAINER = re.compile(
    r"^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:\{\s*\}|\[\s*\]|new\s+(?:Map|Set|WeakMap|WeakSet)\b)"
)


def find_session_state_violations(source: str) -> list[str]:
    """Return one message per top-level declaration that can hold session data."""
    violations: list[str] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        match = _TOP_LEVEL_LET.match(line)
        if match and match.group(1) not in ALLOWED_TOP_LEVEL_LETS:
            violations.append(f"line {line_number}: module-level variable '{match.group(1)}'")
            continue
        match = _TOP_LEVEL_EMPTY_CONTAINER.match(line)
        if match:
            violations.append(f"line {line_number}: module-level container '{match.group(1)}'")
    return violations


def card_module_violations(card_module: Path) -> list[str]:
    return find_session_state_violations(card_module.read_text(encoding="utf-8"))


def violation_message(relative_path: str, violations: list[str]) -> str:
    return (
        f"card module {relative_path} keeps state between participant sessions "
        f"({'; '.join(violations)}). Read answers from the DOM or use cardState() "
        "from /static/scripts/cards/session-state.js."
    )
