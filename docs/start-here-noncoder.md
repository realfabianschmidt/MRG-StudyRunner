# START HERE: Study Runner fuer Nicht-Coder

Diese Datei erklaert, wie du Study Runner lokal nutzt, wo Studien liegen und wie
Updates entstehen.

## Der wichtigste Punkt

`software/` ist das Programm. Hier liegen Server, Admin-Oberflaeche,
Teilnehmerseite, Integrationen und Studieninhalte.

Installierbare Builds sind jetzt Python-only ZIPs. Der alte Tauri-Wrapper ist
nicht mehr Teil des aktiven Projekts.

## Was ist Study Runner?

Study Runner ist eine lokale App fuer Studien. Ein Computer startet den Server,
und Tablets oder andere Browser im gleichen Netzwerk oeffnen die
Teilnehmerseite.

Es gibt zwei Nutzungsarten:

- Lokal fuer Entwicklung: im Ordner `software/` mit `python server.py`.
- Fuer normale Nutzer: ZIP herunterladen, entpacken, `study-runner-server`
  starten.

## Was darf ich anpassen?

Normalerweise arbeitest du hier:

```text
software/study_content/
```

Darin liegen:

- `software/study_content/settings/study_config.json`: aktuelle Standardstudie.
- `software/study_content/settings/hardware_settings.json`: Standardwerte fuer
  Integrationen.
- `software/study_content/studies/`: gespeicherte Studienvorlagen.

Bequemer ist meistens die Admin-Oberflaeche:

```text
http://localhost:3000/admin
```

## Was bitte nicht anfassen?

Diese Ordner sind generiert oder lokal:

- `software/build/`
- `software/dist/`
- `software/saved_results/`

Sie werden durch Tests, Builds oder Studienlaeufe erzeugt und sind nicht die
Quelle der Wahrheit.

## Lokal starten

```powershell
cd software
python server.py
```

Dann im Browser:

```text
http://localhost:3000/admin
```

## ZIP-Build starten

1. GitHub Releases oeffnen:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

2. Passendes ZIP herunterladen:

- Windows: `study-runner-server-windows-x86_64.zip`
- Linux: `study-runner-server-linux-x86_64.zip`
- Mac Intel: `study-runner-server-macos-x86_64.zip`
- Mac Apple Silicon: `study-runner-server-macos-arm64.zip`

3. ZIP entpacken.
4. `study-runner-server.exe` oder `study-runner-server` starten.

Die Admin-Seite oeffnet sich automatisch im Browser.

## Neues Update veroeffentlichen

Kurzantwort: Nein, ein normaler Push auf GitHub reicht nicht fuer ein Update.

Ein Push auf `main` aktualisiert nur den Code im Repository. Ein Update fuer die
App entsteht erst, wenn ein Release-Tag wie `app-v0.2.5` gepusht wird und GitHub
Actions daraus ZIP-Dateien, Hashes, Signaturen und das Update-Manifest gebaut
hat.

Der einfache Weg ist dieses Skript aus dem Hauptordner:

```powershell
.\release.ps1 patch
```

Das Skript:

1. erhoeht die Versionsnummer,
2. fuehrt schnelle lokale Checks aus,
3. committet die Versionsaenderung auf `main`,
4. pusht `main`,
5. pusht einen Tag wie `app-v0.2.5`.

Erst dieser Tag startet GitHub Actions. Wenn der Release-Workflow fertig und
gruen ist, kann die App das Update finden.

## Update am Nutzer-Rechner

In der Admin-Seite:

1. `Check` oder `Pruefen` klicken.
2. Wenn eine neue Version da ist: `Download` klicken.
3. Download bestaetigen.
4. Nach der Pruefung: `Restart` oder `Neustart` klicken.

Der Download startet nie automatisch. Die Person am Rechner muss ihn bestaetigen.

Trockenlauf ohne Veraenderungen:

```powershell
.\release.ps1 patch -DryRun
```

Lokaler Vollcheck mit PyInstaller-Build:

```powershell
.\release.ps1 patch -FullChecks
```

## Was muss fuer Updates eingerichtet sein?

Damit Python-only Updates funktionieren, muessen in GitHub einmal diese Secrets
oder Variables eingerichtet sein:

- `PYTHON_UPDATER_PUBLIC_KEY`
- `PYTHON_UPDATER_SIGNING_PRIVATE_KEY`

Ohne diese Werte baut der Release-Workflow keine vertrauenswuerdigen
Update-Dateien.

## Alte Tauri-Installationen

Alte Tauri-Installationen wechseln nicht automatisch. Einmal das aktuelle
Python-only ZIP herunterladen, entpacken und ab dann dieses Build nutzen.
