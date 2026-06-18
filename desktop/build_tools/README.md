# Build Tools

Technische Build-Konfiguration fuer die Desktop-App.

- `pyinstaller/`: baut den Python-Server aus `software/` als Sidecar, das Tauri in die Desktop-App packt.

Normalerweise wird dieser Ordner nur von CI oder `npm --prefix desktop run build:sidecar` verwendet.
