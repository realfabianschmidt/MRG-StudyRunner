# Integration Plugin Guide

Study Runner uses built-in integration plugins. A plugin is a normal Python folder under `study_runner/integrations/`. There is no automatic discovery and no external plugin marketplace.

## Important Files

- `study_runner/integrations/plugin_api.py`: shared `IntegrationContext` and `IntegrationPlugin` types.
- `study_runner/integrations/registry.py`: explicit list of active built-in plugins.
- `study_content/settings/hardware_settings.json`: persisted plugin settings.
- `study_runner/backend/services/trial_service.py`: sends trial start and stop through the registry.
- `study_runner/backend/services/results_service.py`: asks plugins for interval summaries and sidecar samples.
- `study_runner/backend/services/admin_status_service.py`: builds dashboard status from the registry.

## Required Plugin Shape

```text
study_runner/integrations/my_new_sensor/
  __init__.py
  plugin.py
  adapter.py
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

To enable the plugin, import it explicitly in `study_runner/integrations/registry.py` and add it to the `PLUGINS` tuple. Then add its settings section to `study_content/settings/hardware_settings.json`.

## Lifecycle Callbacks

A plugin can implement only the callbacks it needs:

- `initialize(context)`: prepare clients, streams, or device state.
- `get_status(context)`: return dashboard status.
- `start(context)`, `stop(context)`, `restart(context)`: optional runtime actions.
- `on_trial_start(context, options)`, `on_trial_stop(context, options)`: active stimulus callbacks.
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
- Store backend-local secrets in `study_content/settings/local_secrets.json`.
- Do not add dynamic plugin loading unless the project deliberately changes architecture later.
