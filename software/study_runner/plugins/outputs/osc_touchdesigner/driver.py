"""Executable API-v4 process entrypoint for the osc plugin."""
from study_runner.plugin_framework.driver_runtime import run_plugin_driver


if __name__ == "__main__":
    raise SystemExit(run_plugin_driver("osc"))
