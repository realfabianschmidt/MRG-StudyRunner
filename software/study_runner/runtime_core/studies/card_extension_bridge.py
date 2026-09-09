"""Runtime orchestration for the stateless, process-isolated card contract."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from study_runner.contracts.card_validation_primitives import CardValidationError
from study_runner.plugin_framework.card_catalog import card_bindings
from study_runner.plugin_framework.registry import get_plugin_catalog
from study_runner.plugin_framework.process_host import PluginProcessError

from .study_plugin_config import PluginConfigError, normalize_card_plugin_actions


class CardExtensionUnavailableError(RuntimeError):
    """The card could not validate; the input must not be treated as accepted."""


_defaults_catalog = None
_defaults_cache: dict[str, dict] = {}


def _card_plugin(question_type: str):
    binding = card_bindings().get(question_type)
    if binding is None:
        raise CardExtensionUnavailableError(f"No installed card implements {question_type!r}.")
    entry, contract = binding
    if entry.status != "valid" or entry.plugin is None:
        raise CardExtensionUnavailableError(f"Card {question_type!r} is unavailable: " + "; ".join(entry.errors))
    return entry.plugin, contract


def _call(question_type: str, handler, *args):
    if not callable(handler):
        raise CardExtensionUnavailableError(f"Card {question_type!r} is missing a required handler.")
    try:
        return handler(question_type, *args)
    except PluginProcessError as error:
        if error.error_kind == "invalid_input":
            raise CardValidationError(str(error)) from error
        raise CardExtensionUnavailableError(f"Card {question_type!r} is temporarily unavailable: {error}") from error


def _config_result(question_type: str, result: Any) -> dict:
    if not isinstance(result, dict) or result.get("type") != question_type:
        raise CardExtensionUnavailableError(f"Card {question_type!r} returned an invalid configuration object.")
    return result


def get_card_defaults(question_type: str) -> dict[str, Any]:
    global _defaults_catalog, _defaults_cache
    catalog = get_plugin_catalog()
    if catalog is not _defaults_catalog:
        _defaults_catalog, _defaults_cache = catalog, {}
    plugin, _ = _card_plugin(question_type)
    if question_type not in _defaults_cache:
        value = _config_result(question_type, _call(question_type, plugin.get_card_defaults))
        if catalog is _defaults_catalog:
            _defaults_cache[question_type] = value
        return deepcopy(value)
    return deepcopy(_defaults_cache[question_type])


def defaults_for_extension(plugin_key: str) -> dict[str, dict]:
    types = [t for t, (entry, _) in card_bindings().items() if entry.plugin_key == plugin_key]
    if not types:
        raise CardExtensionUnavailableError(f"Plugin {plugin_key!r} does not provide cards.")
    return {t: get_card_defaults(t) for t in types}


def normalize_card_config(question_type: str, question_data: dict, question_index: int) -> dict:
    plugin, contract = _card_plugin(question_type)
    host_data = {}
    for requirement in contract["host_data"]:
        if requirement == "plugin_actions":
            try:
                host_data[requirement] = normalize_card_plugin_actions(question_data)
            except PluginConfigError as error:
                raise CardValidationError(f"Question {question_index} {error}") from error
    return _config_result(question_type, _call(
        question_type, plugin.normalize_card_config, question_data, question_index, host_data,
    ))


def validate_card_answer(question_type: str, question: dict, answer: Any, question_number: int) -> Any:
    plugin, contract = _card_plugin(question_type)
    if question_type in contract["answerless_types"]:
        raise CardValidationError(f"Question {question_number} does not accept an answer.")
    return _call(question_type, plugin.validate_card_answer, question, answer, question_number)
