# The work behind the HTTP surface

`apps/server/routes/` decides what a URL means and hands off to here. These folders
hold the actual work, grouped by what part of a study they belong to. Nothing
here knows about Flask requests.

| Folder | What it is responsible for |
|---|---|
| `studies/` | A study, its run, and everything the run produces: the config, participants, cards and trials, session storage, per-card summaries, and crash recovery. |
| `delivery/` | Getting a finished session out of the building: finalization, upload queues, Nextcloud, and the HTTPS certificate an operator moves to a tablet. |
| `settings/` | How this machine is configured and what it reports about itself: hardware and plugin settings, secrets, branding, updates, TLS, shortcuts. |

The worker lifecycle, sensor polling, clock synchronization, and recording
quality checks live in `data_core/host/`.

## Responsibility boundaries

Services are grouped by their purpose: delivery services publish results,
study services manage studies and sessions, and settings services manage the
machine. Utilities used across areas live in `study_runner/shared/` and have
no imports from those areas.

- **DataCore does not import RuntimeCore services.** Shared helpers such as
  `atomic_io` are available through `study_runner/shared/`, keeping recording
  code independent of application startup.
- **Runtime paths come from the common path helpers.**
  `settings/runtime_config.py` exposes `get_project_base_dir()` and resolves
  paths for both source and packaged builds.
- **Plugin dispatch uses manifests.** Generic services use declared capabilities
  and settings to select plugin behavior. Historical data conversion is handled
  by the compatibility functions in the corresponding data area.
