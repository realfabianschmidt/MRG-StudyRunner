"""The built-in plugins, one folder each.

Every folder here holds a ``manifest.json`` and is discovered at start-up. There
is no list to register in and no ordering to maintain: what a plugin is, what it
offers, where it appears in the interface, and what it records are all read from
its manifest. Adding one means adding a folder.

The folders are deliberately flat and equal. A plugin's kind (``biosignal``,
``storage``, ``output``, ``sync``) is a field in its manifest, not a directory,
so the interface can group plugins at runtime without anyone having to file a
new plugin under the right parent.

The machinery that reads all of this lives in
:mod:`study_runner.plugin_framework`. See ``README.md`` next to this file for
how to write a plugin.
"""
