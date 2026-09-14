#!/usr/bin/env python3
"""API-v5 process entry point for the archived BrainBit (old) plugin."""
from __future__ import annotations

from study_runner.plugin_framework.driver_runtime import run_plugin_driver


if __name__ == "__main__":
    raise SystemExit(run_plugin_driver("brainbit_old"))
