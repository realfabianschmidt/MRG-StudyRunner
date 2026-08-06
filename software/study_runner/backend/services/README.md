# The work behind the HTTP surface

`routes/` decides what a URL means and hands off to here. These folders hold the
actual work, grouped by what part of a study they belong to. Nothing here knows
about Flask requests.

| Folder | What it is responsible for |
|---|---|
| `recording/` | Getting signal off the hardware and into a file: the worker's lifecycle, the sensor poll loop, LSL dependency checks, clock sync, and whether a recording is good enough to keep. |
| `studies/` | A study, its run, and everything the run produces: the config, participants, cards and trials, session storage, per-card summaries, and crash recovery. |
| `delivery/` | Getting a finished session out of the building: finalization, upload queues, Nextcloud, and the HTTPS certificate an operator moves to a tablet. |
| `settings/` | How this machine is configured and what it reports about itself: hardware and plugin settings, secrets, branding, updates, TLS, shortcuts. |
| `shared/` | Used by all of the above and belonging to none: atomic file writes and study-config validation. |

## Where does a new service go?

Ask what it is *for*, not what it talks to. A service that uploads to a new
destination is `delivery/` even though it reads settings; a service that decides
whether a sensor is ready is `recording/` even though it writes results.

If it genuinely fits two, it usually wants splitting. If it fits none and is
used by several groups, it belongs in `shared/` — but keep that folder small, it
is not a junk drawer.

## Two rules worth keeping

- **Do not compute paths by counting parents.** `settings/runtime_config.py`
  owns `get_project_base_dir()`, and it is frozen-build aware. Three modules had
  their own `parents[3]` and all three broke the moment these files moved one
  folder deeper.
- **No service may name a plugin.** Plugins are discovered from their manifests;
  a service that hardcodes `brainbit` or `notion` has taken a shortcut that stops
  the next plugin from working. `test_sensor_rich_views_and_timeline_preferences_are_plugin_owned`
  guards part of this.
