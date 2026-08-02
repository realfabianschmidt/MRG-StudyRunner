# Start Here: Study Runner fuer Nicht-Coder

Diese Datei erklaert, wie du Study Runner als normalen lokalen Python-Server
nutzt, wo Studien liegen und wie spaetere Release-Pakete entstehen.

## Der wichtigste Punkt

`software/` ist das Programm. Hier liegen Server, Admin-Oberflaeche,
Teilnehmerseite, Integrationen und Studieninhalte.

GitHub Releases enthalten derzeit den geprueften Python-Quellserver als ZIP und
tar.gz. Der alte Tauri-Wrapper und alte App-Installer sind nicht Teil des
aktiven Release-Wegs.

## Was ist Study Runner?

Study Runner ist eine lokale App fuer Studien. Ein Computer startet den Server,
und Tablets oder andere Browser im gleichen Netzwerk oeffnen die
Teilnehmerseite.

Fuer die aktuelle Recording-Architektur ist der Python-Server aus einem
Git-Checkout der bevorzugte Weg. Alternativ kann das Source-Archiv eines GitHub
Releases entpackt werden. Beide Wege verwenden dieselben Installations- und
Startskripte; es gibt aktuell keine separate Desktop-App.

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
- `software/.build/`
- `software/saved_results/`

Sie werden durch Tests, Builds oder Studienlaeufe erzeugt und sind nicht die
Quelle der Wahrheit.

## Software installieren

Der empfohlene Weg ist aktuell der normale Python-Server direkt aus GitHub. Die
Installationsskripte erzeugen eine eigene `.venv`, installieren die festgelegten
Python-Pakete und bauen den kleinen XDF-Kern. Studien und Ergebnisse werden
dabei nicht geloescht. Signing oder Apple-Notarisierung werden dafuer nicht
benoetigt.

Die Skripte verwenden die mitgelieferten Python-3.12-Constraints unter
`software/constraints/`. Damit sind die direkt verwendeten und die besonders
kritischen ML-Versionen fuer den Release-Test festgelegt. Es handelt sich aber
nicht um ein vollstaendiges, hash-gesperrtes Offline-Wheelpaket; deshalb prueft
der GitHub-Release jede Zielplattform noch einmal in einer sauberen Umgebung.

### Windows x64: erste Installation

PowerShell oeffnen und Git installieren, falls es noch fehlt:

```powershell
winget install --id Git.Git --exact --source winget
```

Danach ein neues PowerShell-Fenster oeffnen und ausfuehren:

```powershell
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
.\tools\install-windows.ps1 -InstallSystemDependencies
```

Das Skript installiert fehlendes Python 3.12, CMake und die Visual-Studio-C++-
Build-Tools ueber WinGet. Windows kann dabei nach Administratorrechten fragen.
Blockiert PowerShell lokale Skripte, funktioniert einmalig:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\install-windows.ps1 -InstallSystemDependencies
```

### Windows: spaeter starten

Im Projektordner genuegt:

```powershell
.\tools\start-windows.ps1
```

### macOS Intel oder Apple Silicon: erste Installation

Im Terminal zuerst Apples Command Line Tools anfordern und den Dialog komplett
abschliessen:

```bash
xcode-select --install
```

Danach Homebrew mit seinem offiziellen Installer installieren und die dort
angezeigten `Next steps` fuer die Shell ausfuehren:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Dann Study Runner einrichten:

```bash
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
bash tools/install-macos.sh --install-system-dependencies
```

Das Skript fuehrt wiederholbar `brew install python@3.12 cmake` aus und baut den
XDF-Kern mit Apples Compiler.

Auf Apple Silicon kann `camera_emotion` den lokalen DeepFace-Worker verwenden.
Fuer Mac Intel gibt es mit Python 3.12 aktuell keine passenden
TensorFlow/tf-keras-Wheels. Die Intel-Installation unterstuetzt Server und XDF-
Recording vollstaendig, fuer Kamera/Emotion muss aber `remote_worker` mit einem
anderen Analyse-Rechner konfiguriert werden.

### macOS: spaeter starten

Im Projektordner genuegt:

```bash
bash tools/start-macos.sh
```

Die `.venv` muss auf keiner Plattform manuell aktiviert werden. Der Startbefehl
verwendet immer direkt den richtigen Python-Interpreter. Danach ist Admin hier:

```text
https://localhost:3000/admin
```

### Aktualisieren oder Installation reparieren

Zuerst den Code aktualisieren:

```bash
git pull --ff-only
```

Danach das passende Installationsskript noch einmal ohne System-Schalter
ausfuehren. Es aktualisiert die Python-Abhaengigkeiten und verwendet einen
bereits gueltigen XDF-Kern weiter:

```powershell
.\tools\install-windows.ps1
```

```bash
bash tools/install-macos.sh
```

Nur fuer eine Installation ohne Sensoraufzeichnung gibt es
`-SkipRecordingCore` beziehungsweise `--skip-recording-core`. Studien ohne XDF
laufen dann, Pflicht-Recording bleibt jedoch mit einem klaren Hinweis blockiert.

Spaetere Release-ZIPs und ein Installations-Wizard sind ein getrennter Weg. Die
aktuelle Source-Installation braucht weder signierte Pakete noch Apple-
Notarisierung.

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
- Kamera und Emotion gemeinsam als Plugin `camera_emotion`; lokaler oder
  entfernter Analyse-Worker sind nur Betriebsarten dieses Plugins.
- LSL als gemeinsamer Datenweg und der Python-Recording-Worker mit kleinem
  XDF-Kern fuer synchronisierte Rohdaten.
- Notion Upload fuer kompakte Zusammenfassungen, wenn ein API-Key gesetzt ist.

Wichtig: Kamera-Emotion streamt Livebilder ins Dashboard, sobald Camera Emotion
effektiv aktiv ist, die Tablet-Seite offen ist und die Kamera erlaubt wurde.
Vor dem Studienstart werden diese Bilder nur fuer den Live-Monitor genutzt.
Gespeichert wird erst nach gueltiger Participant ID und Studienstart.

Die Sensor-Auswahl in den Studien-Einstellungen ist der gespeicherte Standard.
Das Dashboard darf diese Auswahl temporaer fuer die aktuelle Server-Session
ueberstimmen. Das ist praktisch fuer Tests im Labor. Mit `Reset to study
settings` faellt alles wieder auf die gespeicherten Studienwerte zurueck.

Auf Windows x64 und Mac Apple Silicon werden DeepFace, TensorFlow/tf-keras,
OpenCV und der lokale Emotion Worker vom Installationsskript aus
`software/requirements.txt` in `.venv` installiert. Das separat lizenzierte
Emotion-Modell wird weder mitgeliefert noch still heruntergeladen. Pruefe zuerst
`THIRD_PARTY_NOTICES.md`. Wenn die dort verlinkten Bedingungen fuer
nicht-kommerzielle Forschung zur Studie passen, stelle das gepinnte Modell mit
`python release_tools/fetch_deepface_model_assets.py
--accept-vgg-face-non-commercial-research-terms` bereit; der SHA-256-Hash wird
geprueft. Andernfalls wird `remote_worker` mit einem entsprechend lizenzierten
Modell verwendet. Mac Intel nutzt fuer die Analyse immer `remote_worker`.

WLAN- und LAN-Sensoren liefern LSL direkt. BLE uebertraegt selbst kein LSL:
Der lokale BLE-Adapter empfaengt die Pakete und stellt sie danach als LSL-Stream
bereit. Browserquellen benoetigen HTTPS, Heartbeat, Sequenznummer und Quellzeit.

Beim Klick auf Submit werden die Antworten zuerst lokal sicher geschrieben.
Danach sieht der Participant bereits die Abschlussseite. Im Admin-Fenster laufen
XDF-Abschluss, Quellenpruefung, Merge, Statistik, Notion und Nextcloud sichtbar
im Hintergrund weiter. Ein Fehler wird als `attention_required` angezeigt und
nie still als Erfolg behandelt.

## Neues Update veroeffentlichen

Kurzantwort: Nein, ein normaler Push auf GitHub reicht nicht fuer ein Update.

Ein Push auf `main` aktualisiert nur den Code im Repository. Ein oeffentlicher
Source-Release entsteht erst, wenn ein Release-Tag wie `app-v0.4.1` gepusht
wird. GitHub Actions baut daraus ZIP und tar.gz, Metadaten und SHA-256-Summen.

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

Erst dieser Tag startet GitHub Actions. Veroeffentlicht wird nur, wenn die echte
Installation und die nativen XDF-Smoke-Tests auf Windows x64, Mac Intel und Mac
Apple Silicon erfolgreich sind.

## Update am Nutzer-Rechner

Der aktuelle Source-Server aktualisiert sich nicht selbst. Server stoppen und
im Projektordner ausfuehren:

```powershell
git pull --ff-only
.\tools\install-windows.ps1
.\tools\start-windows.ps1
```

Auf dem Mac:

```bash
git pull --ff-only
bash tools/install-macos.sh
bash tools/start-macos.sh
```

Die Installationsskripte verwenden `.venv` weiter, aktualisieren Pakete und
bauen nur einen fehlenden oder veralteten XDF-Kern neu. Studien und Ergebnisse
werden nicht geloescht. Wer statt Git ein neues Source-Archiv entpackt, muss den
alten Datenordner sichern oder vorher `STUDY_RUNNER_DATA_DIR` ausserhalb des
Programmordners setzen.

Trockenlauf ohne Veraenderungen:

```powershell
.\release.ps1 patch -DryRun
```

Lokaler Vollcheck inklusive nativem XDF-Core:

```powershell
.\release.ps1 patch -FullChecks
```

## Release-Zugang

Der aktuelle Workflow benoetigt keine Apple-Credentials, Notarisierung oder
Updater-Schluessel. Das eingebaute GitHub-Token darf nur die bereits geprueften
Source-Dateien an den Tag anhaengen. Alte Tauri-, Manager- oder PyInstaller-
Installationen werden nicht automatisch migriert und sind kein aktueller
Recording-Release.

## Weitere Doku

- `docs/operator-guide.md`: taegliche Bedienung im Labor.
- `docs/sensors-and-data.md`: Sensorik, Rohdaten, XDF/LSL und Grenzen.
- `docs/plugin-recording-architecture.md`: kompletter Plugin-, Worker-,
  Finalisierungs- und Recovery-Bauplan.
- `docs/release-and-update.md`: Source-Updates, Release-Dateien und Abnahme-Gates.
- `docs/developer-guide.md`: Struktur und Regeln fuer Code-Aenderungen.
