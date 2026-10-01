# Notes for coding agents

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing anything. It is short and every rule applies. Code comments and developer documents are written in English.

Rules that are easy to miss:

- **Plugin versions.** Changed any file below `software/study_runner/plugins/`? Raise that plugin's `"version"` in its `manifest.json`: MAJOR when the recorded data changes meaning (stream, channel, unit, rate, backup projection, answer format), MINOR for a feature that leaves existing data unchanged, PATCH for a fix with no effect on data. Then run `python tools/plugin_versions.py --update` and commit the lock file with the change. Researchers cite these versions in their papers, see CONTRIBUTING.md section 11.
- **One pattern for all plugins.** Fix a problem once in the shared core or helper so that every plugin of that kind follows it, update the templates in `tools/plugin_templates/`, and add a test that holds every plugin to it.
- **Check before calling work done.** From `software/`: `python -m pytest -q -p no:cacheprovider` and `node --test tests/js/*.test.mjs`. From the repository root: `python -m pytest release_tools/tests` and `python tools/measure_structure.py --check`.
