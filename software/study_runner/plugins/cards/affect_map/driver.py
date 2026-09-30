"""Run the Affect Map card in the common extension worker."""
from study_runner.plugin_framework.driver_runtime import run_plugin_driver

if __name__ == "__main__":
    raise SystemExit(run_plugin_driver("affect_map"))
