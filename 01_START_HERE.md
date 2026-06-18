# START HERE: Study Runner fuer Nicht-Coder

Diese Datei erklaert, was du in Study Runner normalerweise anfassen darfst und was nur die technische Verpackung ist.

## Der wichtigste Punkt

Das Projekt hat zwei Haelften:

- `software/` ist **das Programm**. Hier aenderst du Python, die Oberflaeche, Sensoren und Studien.
- `desktop/` ist **die installierbare Huelle** (Tauri). Die fasst du fast nie an.

Faustregel: Du arbeitest fast immer nur in `software/`.

## Was ist Study Runner?

Study Runner ist eine lokale App fuer Studien. Ein Computer startet den Server, und Tablets oder andere Browser im gleichen Netzwerk oeffnen die Teilnehmerseite.

Es gibt zwei Nutzungsarten:

- Browser-Modus: im Ordner `software/` mit `python server.py`.
- Desktop-App: installierte Windows-, macOS- oder Linux-App mit Update-Funktion.

## Was darf ich anpassen?

Normalerweise arbeitest du hier:

```text
software/study_content/
```

Darin liegen:

- `software/study_content/settings/study_config.json`: aktuelle Standardstudie.
- `software/study_content/settings/hardware_settings.json`: Standardwerte fuer Integrationen.
- `software/study_content/studies/`: gespeicherte Studienvorlagen.

Du kannst Studien aber meist bequemer ueber die Admin-Oberflaeche bearbeiten:

```text
http://localhost:3000/admin
```

## Was ist die eigentliche App?

Die App-Logik liegt hier:

```text
software/study_runner/
```

Darin liegen Backend, Browseroberflaeche und eingebaute Integrationen. Diesen Bereich nur bearbeiten, wenn Verhalten, UI oder Integrationen geaendert werden sollen.

## Was ist die Desktop-Huelle?

Die installierbare App entsteht aus dieser Huelle:

```text
desktop/
```

Diese Huelle startet den gebuendelten Python-Server und zeigt den Launcher. Sie ist nicht der Ort fuer normale Studieninhalte.

## Was bitte nicht anfassen?

Diese Ordner sind generiert oder lokal:

- `software/build/`
- `software/dist/`
- `software/saved_results/`
- `desktop/node_modules/`
- `desktop/src-tauri/target/`
- `desktop/src-tauri/binaries/`

Sie werden durch Tests oder Builds neu erzeugt und sind nicht die Quelle der Wahrheit.

## Lokal starten

```powershell
cd software
python server.py
```

Dann im Browser:

```text
http://localhost:3000/admin
```

## macOS: erster Start

Die aktuellen Mac-Builds sind nicht von Apple signiert. Beim ersten Start blockiert
macOS sonst den eingebauten Python-Server, und es passiert scheinbar nichts.

Einmal nach der Installation im Terminal ausfuehren:

```bash
xattr -dr com.apple.quarantine "/Applications/Study Runner.app"
```

Danach die App starten. Wenn die Tablets im Netzwerk die Teilnehmerseite nicht erreichen,
erlaube Study Runner unter Systemeinstellungen den Zugriff auf das lokale Netzwerk.

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

Patch-Update starten (aus dem Hauptordner, nicht aus `software/`):

```powershell
.\release.ps1 patch
```

Das Skript bumpet die Version, erstellt einen PR, wartet auf CI, merged bei gruenem Ergebnis, taggt die Version, wartet auf den GitHub-Release und prueft die Update-Datei.

Trockenlauf ohne Veraenderungen:

```powershell
.\release.ps1 patch -DryRun
```
