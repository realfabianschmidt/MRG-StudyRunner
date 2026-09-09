"""Card type lookup derived from the existing extension catalog."""
from __future__ import annotations
from collections.abc import Set
from typing import Any
from . import registry


_bindings_catalog = None
_bindings_cache: dict[str, tuple[Any, dict]] = {}


def card_bindings() -> dict[str, tuple[Any, dict]]:
    """Manifest-driven question_type lookup, cached per catalog generation.

    `QuestionTypes` below (and validation.py through it) calls this once per
    question on every config save/load -- without this cache each call would
    re-walk the whole plugin catalog and re-read every entry's manifest just
    to answer one membership test. A catalog reload builds a new PluginCatalog
    object, so the identity check below still rebuilds exactly once per
    generation; nothing here can go stale.
    """
    global _bindings_catalog, _bindings_cache
    catalog = registry.get_plugin_catalog()
    if catalog is _bindings_catalog:
        return _bindings_cache
    bindings = {}
    for entry in catalog.entries:
        # Invalid entries, including both sides of a duplicate question-type
        # declaration, must never become accepted study question types.
        if entry.status != "valid" or entry.plugin is None:
            continue
        # entry.manifest is the *normalized* shape, where each capability's
        # config lives under "capability_config" by name -- a different key
        # entirely from the plain-list "capabilities" that same manifest also
        # carries. See the comment on PluginProcessRuntime.is_card for the
        # other two shapes the same question is answered with elsewhere.
        contract = ((entry.manifest or {}).get("capability_config") or {}).get("card_contract")
        if not contract:
            continue
        for question_type in contract["question_types"]:
            bindings[question_type] = (entry, contract)
    _bindings_catalog, _bindings_cache = catalog, bindings
    return bindings


class QuestionTypes(Set):
    """A live view, so catalog reload never leaves imported type sets stale."""
    def __init__(self, *, answerless: bool = False):
        self.answerless = answerless

    def __iter__(self):
        return iter(t for t, (_, c) in card_bindings().items()
                    if not self.answerless or t in c["answerless_types"])

    def __contains__(self, value):
        return value in set(iter(self))

    def __len__(self):
        return sum(1 for _ in self)

    @classmethod
    def _from_iterable(cls, values):
        return set(values)
