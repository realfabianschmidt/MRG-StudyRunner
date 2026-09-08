"""The machinery that finds, validates, and talks to plugins.

Nothing in here is a plugin. Plugins live one folder over, in
:mod:`study_runner.extensions`, and this package is what turns a folder with a
``manifest.json`` into something the rest of the app can use:

- ``plugin_catalog``  discovery and manifest validation
- ``registry``        the façade the backend calls: look a plugin up, ask it for
                      status, dispatch an action
- ``adapter_utils``   small state-free helpers shared by adapters
- ``history_buffer``  bounded sample history for live views

What a plugin implements -- the context it is handed and the handlers it may
declare -- lives in :mod:`study_runner.contracts.plugin_api`, not here: it is
a plain data/type module both this framework and every plugin import, so it
belongs with the other cross-cutting contracts rather than in the framework
that only one side of that boundary owns.
"""
