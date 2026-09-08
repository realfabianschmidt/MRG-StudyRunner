"""RuntimeCore: studies, sessions, cards, commands, plugin host.

The Flask-independent work behind the HTTP surface (`backend/routes/`
decides what a URL means and hands off here). See README.md for the
per-folder breakdown and the rules for where a new service goes.

RuntimeCore may import `data_core` and `plugin_framework` freely -- that is
the target diagram's allowed direction -- but `data_core` must never import
back into `runtime_core` or `backend`.
"""
