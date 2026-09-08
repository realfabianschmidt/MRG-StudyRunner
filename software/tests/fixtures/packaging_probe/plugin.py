"""Exercise plugin RPC without devices, network calls, or background jobs."""
import os

from study_runner.contracts.plugin_api import Plugin


def _initialize(context):
    if os.environ.get("STUDY_RUNNER_DISABLE_HARDWARE") != "1":
        raise RuntimeError("packaging probe requires hardware isolation")
    if os.environ.get("STUDY_RUNNER_DISABLE_BACKGROUND") != "1":
        raise RuntimeError("packaging probe requires background isolation")


def _status(context):
    return {
        "self_check": True,
        "pid": os.getpid(),
        "data_dir": str(context.data_dir),
        "hardware_disabled": os.environ.get("STUDY_RUNNER_DISABLE_HARDWARE") == "1",
        "background_disabled": os.environ.get("STUDY_RUNNER_DISABLE_BACKGROUND") == "1",
    }


PLUGIN = Plugin(
    key="packaging_probe",
    label="Packaging probe",
    category="output",
    config_key="packaging_probe",
    initialize=_initialize,
    get_status=_status,
)
