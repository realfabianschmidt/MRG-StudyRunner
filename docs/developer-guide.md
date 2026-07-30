# Developer Guide

Study Runner uses built-in integration plugins. A plugin is a normal Python folder under `software/study_runner/integrations/`. There is no automatic discovery and no external plugin marketplace.

## Naming And Structure

- `Software/` is the repository root in the lab workspace.
- `software/` is the app folder and stays lowercase because imports, CI,
  packaging and release tooling depend on it.
- Python packages, modules and services use `snake_case`.
- Browser files and docs use descriptive `kebab-case`.
- Active docs avoid numbered prefixes; archived historical files may keep their
  old names for traceability.
- Keep comments short and useful. Comment why a complex block exists, not what
  every line literally does.

## Important Files

- `software/study_runner/integrations/plugin_api.py`: shared `IntegrationContext` and `IntegrationPlugin` types.
- `software/study_runner/integrations/registry.py`: explicit list of active built-in plugins.
- `software/study_runner/integrations/plugin_manifests.json`: declarative timing/backpressure/stream metadata for each registered plugin.
- `software/study_content/settings/hardware_settings.json`: persisted plugin settings.
- `software/study_runner/backend/services/trial_service.py`: sends trial start and stop through the registry.
- `software/study_runner/backend/services/results_service.py`: asks plugins for interval summaries and sidecar samples.
- `software/study_runner/backend/services/sensor_coordinator_service.py`: central lifecycle/status wrapper over the registry.
- `software/study_runner/backend/services/admin_status_service.py`: builds dashboard status from the coordinator and registry.

## Sensor Hardware Source Files

In the local lab workspace, `../../Sensorik/` is the hardware and vendor/reference
area. It is kept separate from the runtime app so experimental files can stay
visible without becoming app code by accident.

Runtime-ready files that have already been copied into the app integration
folders:

- `../../Sensorik/MR60BHA2/GP_mmwaveBreath_and_Pulse_02/GP_mmwaveBreath_and_Pulse_02.ino` -> `software/study_runner/integrations/mr60_mini_radar/firmware/GP_mmwaveBreath_and_Pulse_02.ino`
- `../../Sensorik/MR60BHA2/README.md` -> `software/study_runner/integrations/mr60_mini_radar/firmware/README.md`
- `../../Sensorik/BrainBit/brainbit_realtime_cli.py` -> `software/study_runner/integrations/brainbit/brainbit_realtime_cli.py`
- `../../Sensorik/BrainBit/README_ENHANCED.md` -> `software/study_runner/integrations/brainbit/README_ENHANCED.md`
- `../../Sensorik/BrainBit/OUTPUT_REFERENCE.md` -> `software/study_runner/integrations/brainbit/OUTPUT_REFERENCE.md`
- `../../Sensorik/BrainBit/HelloEEG_HelloMYO_01.3.toe` -> `software/study_runner/integrations/brainbit/HelloEEG_HelloMYO_01.3.toe`

Do not delete `../../Sensorik/` as part of app cleanup. Delete only generated
runtime artifacts, logs, build output, or explicitly obsolete app folders.

## Required Plugin Shape

```text
software/study_runner/integrations/my_new_sensor/
  __init__.py
  plugin.py
  adapter.py
  tools/       # optional manual diagnostics
  firmware/    # optional microcontroller firmware
```

`plugin.py` must export one object:

```python
from study_runner.integrations.plugin_api import IntegrationPlugin

PLUGIN = IntegrationPlugin(
    key="my_new_sensor",
    label="My new sensor",
    category="biosignal",
    config_key="my_new_sensor",
)
```

To enable the plugin, import it explicitly in `software/study_runner/integrations/registry.py` and add it to the `PLUGINS` tuple. Then add its settings section to `software/study_content/settings/hardware_settings.json` and one manifest entry in `software/study_runner/integrations/plugin_manifests.json`.

## Manifest Contract

Every registered plugin has one JSON manifest entry. The manifest is declarative: it describes what the coordinator should assume, but it does not replace the Python callbacks.

Required fields after normalization:

- `capabilities`: status, runtime, recording, LSL, upload, processing, or repair abilities.
- `streams`: source streams with `key`, `clock_domain`, format, and timestamp origin.
- `poll_interval_ms`: how often coordinator/admin status should poll that plugin.
- `request_timeout_ms`: status/request timeout budget.
- `clock_domain`: `lsl`, `server`, `tablet_performance`, or another explicit domain.
- `backpressure`: at least `max_in_flight` and `drop_policy`.
- `runtime_settings`: machine-level settings that belong in the Settings hub, not per-study sensor selection.

For scientific stream alignment, keep LSL/XDF timestamps and source timestamps authoritative. Coordinator timing is for lifecycle, status, diagnostics, browser/worker RTT, and non-LSL metadata.

## Lifecycle Callbacks

A plugin can implement only the callbacks it needs:

- `initialize(context)`: prepare clients, streams, or device state.
- `get_status(context)`: return dashboard status.
- `start(context)`, `stop(context)`, `restart(context)`: optional runtime actions.
- `on_trial_start(context, options)`, `on_trial_stop(context, options)`: active stimulus callbacks.
- `on_trial_marker(context, options)`: marker-only lifecycle events such as study start, question shown, or question answered.
- `get_interval_summary(context, start, end)`: compact answer-level sensor summary.
- `export_interval_samples(context, start, end)`: raw or sensor-near sidecar export.

## Standard Status Fields

The registry normalizes common fields:

- `key`
- `label`
- `category`
- `config_key`
- `configured_enabled`
- `runtime_enabled`
- `status`
- `last_message`
- `device_label`
- `can_start`
- `can_stop`
- `can_restart`
- `can_toggle`
- `has_lsl`
- `has_recording`

## Rules

- Keep adapter code specific to one external tool or device.
- Keep plugin registry entries explicit.
- Keep secrets out of browser responses and Git-tracked settings.
- Store backend-local secrets in `software/study_content/settings/local_secrets.json`.
- Do not add dynamic plugin loading unless the project deliberately changes architecture later.
- UI text lives in `software/study_runner/web/locales/en.json` and `de.json`;
  both files must keep the same keys.
