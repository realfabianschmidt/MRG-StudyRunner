# Build Tools

This folder contains technical build configuration for the desktop app.

- `pyinstaller/`: builds the Python app from `software/` into the server sidecar that Tauri bundles with the launcher.

Most development does not happen here. These files are normally used by CI or by:

```bash
npm --prefix desktop run build:sidecar
```
