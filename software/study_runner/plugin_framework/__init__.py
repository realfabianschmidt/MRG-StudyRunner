"""The machinery that finds, validates, and talks to plugins.

Nothing in here is a plugin. Plugins live one folder over, in
:mod:`study_runner.plugins`, and this package is what turns a folder with a
``manifest.json`` into something the rest of the app can use:

- ``plugin_api``      what a plugin implements: the context it is handed and the
                      handlers it may declare
- ``plugin_catalog``  discovery and manifest validation
- ``registry``        the façade the backend calls: look a plugin up, ask it for
                      status, dispatch an action
- ``adapter_utils``   small state-free helpers shared by adapters
- ``dependency_utils`` optional-import probing, so a missing SDK degrades to an
                      unavailable plugin instead of a crash
- ``history_buffer``  bounded sample history for live views

A plugin imports from here by absolute path
(``from study_runner.plugin_framework.plugin_api import ...``) so that the
framework's public surface is visible in every plugin's import block.
"""
