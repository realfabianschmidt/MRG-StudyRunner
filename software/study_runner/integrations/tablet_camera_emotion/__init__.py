"""One-version import compatibility for the canonical camera plugin."""

import sys

from ..camera_emotion import adapter, plugin

PLUGIN = plugin.PLUGIN
sys.modules[f"{__name__}.adapter"] = adapter
sys.modules[f"{__name__}.plugin"] = plugin

__all__ = ["PLUGIN"]
