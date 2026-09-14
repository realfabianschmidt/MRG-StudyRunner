# Settings

Everything that belongs to *this computer* rather than to any one study:
hardware/plugin configuration, secrets, branding, the app's own update
mechanism, local HTTPS, and the runtime paths every other part of the app
resolves against. None of this travels when a study is exported to another
machine — that's the per-study counterpart in `../studies/`.

## Files

| File | What it does | In / Out |
|---|---|---|
| `migrate.py` | Recursively rewrites stored plugin paths from historical flat or categorized layouts to `plugins/<category>/<name>/`, preserving already-current values. | nested saved values to migrated values and a change count |
| `runtime_config.py` | Where every path and network setting comes from: the data directory, the config files' locations, the host/port, whether this is a packaged or source install. The foundation everything else in this folder (and much of the rest of the app) builds on. | environment variables + install layout → `RuntimePaths`, ports, app mode |
| `hardware_settings_service.py` | Reads and atomically updates `hardware_settings.json` (revision-checked, so two simultaneous admin saves can't silently overwrite each other), and rewrites plugin paths left over from not one but two historical folder renames (`integrations/` → `plugins/` → `plugins/<category>/`) so an operator's existing install keeps working. | the on-disk hardware config → the current config, safely updated |
| `plugin_settings_service.py` | Turns a plugin's manifest-declared settings schema plus the actual on-disk values into what the admin settings panel shows and can save — reading a dotted path, writing only what actually changed, manifest defaults filling gaps but never overwriting what's already saved. | a plugin's settings schema + saved values → the effective settings, safely merged |
| `secrets_service.py` | Where credentials that must *not* travel with an exported study live (API keys, passwords): atomic read/modify/write, and redacting a hardware config down to "is a secret configured, and from where" before it's ever sent to a browser. | `local_secrets.json` ↔ redacted/full config views |
| `branding_service.py` | What the operator's uploaded group logo and funder logos are allowed to be (file type, size, how many), and the one place that turns a slot name into a real file path — never by trusting a caller-supplied name directly, which is what keeps the serving route safe from path traversal. | an uploaded image → a validated, stored branding asset |
| `admin_status_service.py` | Assembles the one compact status payload the admin dashboard polls: plugin statuses, connected tablets, clock-sync summary. | the sensor coordinator + clock sync service → one status object |
| `folder_open_service.py` | Validates a study/participant or session path against the data directory (rejecting any path-traversal attempt) and then asks the operating system to open it in its file browser. | a study/participant id or session path → the OS's file explorer opens it |
| `shortcut_service.py` | Creates a desktop shortcut that starts Study Runner — a small PowerShell script on Windows, a shell script on macOS. | the app's own install location → a `.lnk`/`.command` file on the Desktop |
| `ssl_service.py` | Study Runner's own local HTTPS certificate authority: creates a root CA once, then issues and reissues a server certificate for whatever hostnames/IPs this computer currently has. Needed because a tablet's camera only works over HTTPS, and the lab network has no internet to get a real one from. | the current hostnames/IPs → a locally-trusted certificate chain |
| `update_service.py` | The in-app updater, and the biggest file here: checks for a new version, downloads and verifies a signed packaged build (or, in source-mode installs, runs `git pull` plus the install script instead), and hands off to a detached installer so the update can replace files the running process itself can't touch. | the current version + a published release manifest → an update applied (or a clear reason it can't be) |
| `__init__.py` | One-paragraph orientation for the whole folder. | — |
