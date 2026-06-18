# START HERE: Study Runner fuer Nicht-Coder

Diese Datei erklaert, was du in Study Runner normalerweise anfassen darfst und was nur die technische Verpackung ist.

## Was ist Study Runner?

Study Runner ist eine lokale App fuer Studien. Ein Computer startet den Server, und Tablets oder andere Browser im gleichen Netzwerk oeffnen die Teilnehmerseite.

Es gibt zwei Nutzungsarten:

- Browser-Modus: direkt aus diesem Ordner mit `python server.py`.
- Desktop-App: installierte Windows-, macOS- oder Linux-App mit Update-Funktion.

## Was darf ich anpassen?

Normalerweise arbeitest du hier:

```text
study_content/
```

Darin liegen:

- `study_content/settings/study_config.json`: aktuelle Standardstudie.
- `study_content/settings/hardware_settings.json`: Standardwerte fuer Integrationen.
- `study_content/studies/`: gespeicherte Studienvorlagen.

Du kannst Studien aber meist bequemer ueber die Admin-Oberflaeche bearbeiten:

```text
http://localhost:3000/admin
```

## Was ist die eigentliche App?

Die App-Logik liegt hier:

```text
study_runner/
```

Darin liegen Backend, Browseroberflaeche und eingebaute Integrationen. Diesen Bereich nur bearbeiten, wenn Verhalten, UI oder Integrationen geaendert werden sollen.

## Was ist die Desktop-Huelle?

Die installierbare App entsteht aus dieser Huelle:

```text
desktop_wrapper/
```

Diese Huelle startet den gebuendelten Python-Server und zeigt den Launcher. Sie ist nicht der Ort fuer normale Studieninhalte.

## Was bitte nicht anfassen?

Diese Ordner sind generiert oder lokal:

- `build/`
- `dist/`
- `saved_results/`
- `desktop_wrapper/node_modules/`
- `desktop_wrapper/src-tauri/target/`
- `desktop_wrapper/src-tauri/binaries/`

Sie werden durch Tests oder Builds neu erzeugt und sind nicht die Quelle der Wahrheit.

## Lokal starten

```powershell
python server.py
```

Dann im Browser:

```text
http://localhost:3000/admin
```

## Neues Update veroeffentlichen

Voraussetzung: GitHub CLI ist installiert und angemeldet.

Installation unter Windows:

```powershell
winget install --id GitHub.cli
```

Einmalig pruefen:

```powershell
gh auth login
gh auth status
```

Patch-Update starten:

```powershell
.\release.ps1 patch
```

Das Skript bumpet die Version, erstellt einen PR, wartet auf CI, merged bei gruenem Ergebnis, taggt die Version, wartet auf den GitHub-Release und prueft die Update-Datei.

Trockenlauf ohne Veraenderungen:

```powershell
.\release.ps1 patch -DryRun
```
