"""Utilities every area may use, and that depend on no area.

Nothing here may import from ``backend``, ``recording``, ``plugins`` or
``frontend``. That is the whole point: ``recording`` needs crash-safe writes and
so does the backend, and when the helper lived under ``backend.services`` the
recording package could not be imported without constructing the Flask app --
which made a genuine import cycle and broke any tool that only wanted to read a
session.

Keep this small. A module belongs here only when more than one area needs it and
it belongs to none of them.
"""
