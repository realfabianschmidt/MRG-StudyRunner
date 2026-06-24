# Desktop Wrapper

This folder turns Study Runner into an installable desktop app.

It contains:

- Tauri configuration and the Rust wrapper in `src-tauri/`.
- Launcher UI files in `web/`.
- Build and signing helper scripts in `scripts/`.
- PyInstaller sidecar build files in `build_tools/`.

The actual Study Runner app lives next to this folder in `software/`.
Use `software/server.py` as the local browser-mode entrypoint. The desktop wrapper should only be changed when the launcher, updater, packaging, or signing flow changes.
