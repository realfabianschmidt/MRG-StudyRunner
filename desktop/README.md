# Desktop Wrapper

Dieser Ordner macht aus Study Runner eine installierbare Desktop-App.

Er enthaelt:

- Tauri-Konfiguration und Rust-Wrapper in `src-tauri/`.
- Launcher-UI in `web/`.
- Hilfsskripte in `scripts/`.

Die eigentliche Study-Runner-App liegt im Nachbarordner `software/`:
`software/study_runner/` enthaelt den Code, `software/study_content/` die Studieninhalte.
Dieser Wrapper-Ordner wird nur angefasst, wenn der Launcher selbst geaendert werden soll.
