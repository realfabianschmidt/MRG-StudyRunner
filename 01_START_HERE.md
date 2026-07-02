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

Kurzantwort: Nein, ein normaler Push auf GitHub reicht nicht fuer ein Update.

Ein Push auf `main` aktualisiert nur den Code im Repository. Ein Update fuer die
installierte App entsteht erst, wenn ein Release-Tag wie `app-v0.2.3` gepusht
wird und GitHub Actions daraus die Installationsdateien, Signaturen und
Update-Dateien gebaut hat.

Der einfache Weg ist deshalb: nicht manuell taggen, sondern dieses Skript nutzen.

Patch-Update starten (aus dem Hauptordner, nicht aus `software/`):

```powershell
.\release.ps1 patch
```

Das Skript:

1. erhoeht die Versionsnummer,
2. fuehrt schnelle lokale Checks aus,
3. committet die Versionsaenderung auf `main`,
4. pusht `main`,
5. pusht einen Tag wie `app-v0.2.3`.

Erst dieser Tag startet GitHub Actions. Dort werden Windows-, Linux- und
Mac-Dateien gebaut und als GitHub Release veroeffentlicht.

Wenn der Release-Workflow fertig und gruen ist, kann die App das Update finden:

- In der aktuellen Tauri-Desktop-App erscheint das Update im Desktop-Launcher.
- In Python-only Builds erscheint das Update in der Admin-Seite im Update-Kasten.
  Dort klickt man zuerst auf `Pruefen`, dann auf `Download`, und erst danach auf
  `Neustart`.

Wichtig: Der Download startet nie automatisch. Die Person am Rechner muss ihn in
der Oberflaeche bestaetigen.

Trockenlauf ohne Veraenderungen:

```powershell
.\release.ps1 patch -DryRun
```

Lokaler Vollcheck mit Sidecar- und Rust-Check:

```powershell
.\release.ps1 patch -FullChecks
```

## Was muss vor dem ersten Python-only Update eingerichtet sein?

Damit Python-only Updates funktionieren, muessen in GitHub einmal zwei Secrets
oder Variables eingerichtet sein:

- `PYTHON_UPDATER_PUBLIC_KEY`
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`

Ohne diese Werte baut der Release-Workflow keine vertrauenswuerdigen
Python-Update-Dateien. Die Tauri-Updates nutzen weiterhin ihre eigenen
Tauri-Signing-Secrets.
