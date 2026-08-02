"""One-version import and CLI compatibility for the internal camera worker."""

import sys

from ..camera_emotion.worker import analyzer, model_errors, plugin, server

PLUGIN = plugin.PLUGIN
sys.modules[f"{__name__}.analyzer"] = analyzer
sys.modules[f"{__name__}.model_errors"] = model_errors
sys.modules[f"{__name__}.plugin"] = plugin
sys.modules[f"{__name__}.server"] = server

__all__ = ["PLUGIN"]
