# Study Content

This folder contains editable default content.

- `settings/study_config.json`: active default study.
- `settings/hardware_settings.json`: default integration settings.
- `studies/`: reusable `.study-runner` study presets.

## Your own study must not be committed

`settings/study_config.json` is tracked **and** is the file the running app
rewrites whenever you load or edit a study. So working on a real study shows up
as a modification to a tracked file, one `git commit -a` away from publishing
your study design in a public repository.

Only the two `Example *.study-runner` presets and the shipped example in
`study_config.json` belong in the repository. Everything else under `studies/`
is gitignored, which is where your own work belongs: save the study as a preset
from the admin page, then put the template back before committing.

```bash
git checkout -- software/study_content/settings/study_config.json
```

`software/tests/test_shipped_study_content.py` fails if the active study is not
one of the shipped examples, so this cannot slip through unnoticed. Saved
results are handled the same way: everything under `saved_results/` is ignored
except the curated `Demo_Completed_Study`.

Study settings store the intended sensor defaults for a study. During a lab
session, the Admin dashboard can apply temporary sensor overrides without
changing these files. Use the dashboard reset action to return to saved study
settings.

In packaged mode with an external data folder, these files are copied into the app-data folder only on first start. Later updates do not overwrite local user studies, local settings, saved results, or local secrets.
