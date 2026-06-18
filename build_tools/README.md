# Build Tools

Technische Build-Konfiguration fuer die Desktop-App.

- `pyinstaller/`: baut den Python-Server als Sidecar, das Tauri in die Desktop-App packt.

Normalerweise wird dieser Ordner nur von CI oder `npm --prefix desktop_wrapper run build:sidecar` verwendet.
