# Start Here: Study Runner fuer Nicht-Coder

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
https://localhost:3000/admin
```

## Was bitte nicht anfassen?

Diese Ordner sind generiert oder lokal:

- `software/build/`
- `software/dist/`
- `software/saved_results/`

Sie werden durch Tests, Builds oder Studienlaeufe erzeugt und sind nicht die
Quelle der Wahrheit.

## Software installieren

Es gibt zwei sinnvolle Wege:

- Einfach fuer Labor-Nutzung: Install & Repair Wizard von GitHub Releases herunterladen.
- Flexibel fuer Entwicklung oder schnelle Tests: GitHub-Repository klonen und
  Python-Abhaengigkeiten installieren.

### Wizard fuer normale Nutzung

1. GitHub Releases oeffnen:

```text
https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest
```

2. Passendes ZIP herunterladen:

- Windows: `study-runner-manager-windows-x86_64.zip`
- Mac Intel: `study-runner-manager-macos-x86_64.zip`
- Mac Apple Silicon: `study-runner-manager-macos-arm64.zip`

3. ZIP entpacken.
4. `study-runner-manager.exe` oder `study-runner-manager` starten.
5. Im Wizard `Install / Update Study Runner` klicken.
6. Danach `Create desktop launcher` oder `Start Study Runner` nutzen.

Der Wizard installiert den neuesten stabilen signierten Study-Runner-Release,
legt App und Daten getrennt ab und kann eine kaputte Installation reparieren,
ohne den Datenordner zu loeschen.

Wenn der Server laeuft, ist die Admin-Seite hier:

```text
https://localhost:3000/admin
```

Manuelle Alternative: Du kannst weiterhin direkt ein
`study-runner-server-*.zip` herunterladen, entpacken und
`study-runner-server(.exe)` starten. Fuer Nicht-Coder ist aber der Manager der
robustere Standardweg.

### GitHub-Version fuer Entwicklung

Windows PowerShell:

```powershell
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r software\requirements.txt
cd software
python server.py
```

macOS Terminal:

```bash
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r software/requirements.txt
cd software
python server.py
```

Dann im Browser:

```text
https://localhost:3000/admin
```

## HTTPS und iPad / Tablet Kamera

Die Tablet-Kamera funktioniert im Browser nur zuverlaessig ueber HTTPS. Study
Runner startet deshalb standardmaessig mit HTTPS und erzeugt beim ersten Start
auf jedem Server-Rechner ein eigenes lokales Zertifikat.

Beim Start steht in der Konsole eine Zeile wie:

```text
iPad trust certificate: ...\study-runner-local-root-ca.crt
```

Diese Datei gehoert nur zu diesem Rechner. Wenn du Study Runner auf einem
anderen Windows-PC oder Mac startest, wird dort ein neues Zertifikat erzeugt und
das Tablet muss diesem neuen Zertifikat ebenfalls vertrauen.

Einrichtung auf dem iPad:

1. `study-runner-local-root-ca.crt` aufs iPad uebertragen. Wenn iPadOS die Datei
   nicht als Zertifikat erkennt, die Kopie in `.cer` umbenennen.
2. In iPadOS installieren unter:

```text
Einstellungen > Allgemein > VPN & Geraeteverwaltung
```

3. Danach die Root-CA voll vertrauen unter:

```text
Einstellungen > Allgemein > Info > Zertifikatsvertrauenseinstellungen
```

4. Danach die in der Konsole angezeigte Tablet-Adresse oeffnen:

```text
https://<computer-ip>:3000
```

Wenn `Zertifikatsvertrauenseinstellungen` nicht erscheint, ist noch kein
zusaetzliches Zertifikat installiert. Das entspricht Apples Hinweis fuer manuell
installierte Root-Zertifikate:

```text
https://support.apple.com/en-us/102390
```

## Sensorik im Labor

Study Runner kann aktuell diese Sensoren/Integrationen nutzen:

- BrainBit EEG ueber Bluetooth/NeuroSDK.
- MR60 Radar ueber ESP32-C6 BLE-Firmware.
- Tablet-Kamera-Emotion ueber die normale Teilnehmerseite und den lokalen
  DeepFace Worker.
- LSL Marker und LabRecorder/XDF fuer synchronisierte Rohdaten.
- Notion Upload fuer kompakte Zusammenfassungen, wenn ein API-Key gesetzt ist.

Wichtig: Kamera-Emotion streamt Livebilder ins Dashboard, sobald Camera Emotion
effektiv aktiv ist, die Tablet-Seite offen ist und die Kamera erlaubt wurde.
Vor dem Studienstart werden diese Bilder nur fuer den Live-Monitor genutzt.
Gespeichert wird erst nach gueltiger Participant ID und Studienstart.

Die Sensor-Auswahl in den Studien-Einstellungen ist der gespeicherte Standard.
Das Dashboard darf diese Auswahl temporaer fuer die aktuelle Server-Session
ueberstimmen. Das ist praktisch fuer Tests im Labor. Mit `Reset to study
settings` faellt alles wieder auf die gespeicherten Studienwerte zurueck.

DeepFace: Das Python-Paket wird mit `software/requirements.txt` installiert.
Das wichtige Emotion-Modell `facial_expression_model_weights.h5` liegt bereits
im Repository und in Release-Builds. Normalerweise muss es also nicht separat
von GitHub heruntergeladen werden.

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

## Weitere Doku

- `docs/operator-guide.md`: taegliche Bedienung im Labor.
- `docs/sensors-and-data.md`: Sensorik, Rohdaten, XDF/LSL und Grenzen.
- `docs/release-and-update.md`: Updates, Release-ZIPs und Shortcut-Details.
- `docs/developer-guide.md`: Struktur und Regeln fuer Code-Aenderungen.
