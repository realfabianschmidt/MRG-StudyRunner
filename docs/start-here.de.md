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

Fuer Nicht-Entwickler ist das Source-Archiv eines GitHub Releases der einfachste
Weg. Ein Git-Checkout bleibt die Alternative fuer Personen, die spaeter mit
`git pull` im selben Ordner aktualisieren wollen. Beide Wege verwenden dieselben
Installations- und Startskripte; es gibt aktuell keine separate Desktop-App.

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

- `software/.build/`
- `software/saved_results/`

Sie werden durch Tests, Builds oder Studienlaeufe erzeugt und sind nicht die
Quelle der Wahrheit.

## Software installieren

Fuer die einfachste Installation ohne Git lade das gepruefte Archiv vom
[aktuellen GitHub-Release](https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest)
herunter. Verschiebe den entpackten Ordner vor der Installation aus
`Downloads` in einen dauerhaften Ordner unter **Dokumente**. In diesem Ordner
liegen standardmaessig auch lokale Einstellungen, Studien und Ergebnisse. Den
Ordner deshalb spaeter nicht einfach verschieben oder loeschen.

### Windows x64: erste Installation

1. `study-runner-source.zip` herunterladen und entpacken.
2. Den entpackten Study-Runner-Ordner nach **Dokumente** verschieben.
3. Diesen Ordner im Explorer oeffnen, in die Adresszeile klicken,
   `powershell` eingeben und Enter druecken.
4. Diesen Befehl in PowerShell ausfuehren:

```powershell
.\tools\install-windows.cmd -InstallSystemDependencies
```

5. Warten, bis `Study Runner is ready` erscheint. Die Installation kann einige
   Minuten dauern und Windows kann nach Administratorrechten fragen.
6. Study Runner starten:

```powershell
.\tools\start-windows.cmd
```

Die Admin-Seite oeffnet normalerweise automatisch. Sonst im Browser
`https://localhost:3000/admin` aufrufen.

### Windows: spaeter starten

Im dauerhaften Study-Runner-Ordner `tools\start-windows.cmd` doppelklicken oder
PowerShell wie oben im Ordner oeffnen und ausfuehren:

```powershell
.\tools\start-windows.cmd
```

Das Fenster offen lassen, solange Study Runner laeuft. `Ctrl+C` beendet den
Server.

### macOS 15.6 oder neuer, Intel oder Apple Silicon: erste Installation

1. `study-runner-source.tar.gz` herunterladen. Falls der Browser es nicht
   automatisch entpackt, die Datei im Finder doppelklicken.
2. Den entpackten Study-Runner-Ordner nach **Dokumente** verschieben.
3. Terminal oeffnen und `cd ` inklusive Leerzeichen eingeben. Den Ordner aus
   dem Finder in das Terminalfenster ziehen und Enter druecken.
4. Apples Suche in den geschuetzten Developer Downloads aus dem Terminal
   oeffnen:

```bash
open "https://developer.apple.com/download/all/?q=Xcode%2026.3"
```

5. Mit einem kostenlosen Apple-Account anmelden und **Xcode 26.3 Universal**
   als `Xcode_26.3.xip` herunterladen. Eine kostenpflichtige Developer-
   Mitgliedschaft ist nicht erforderlich. Nicht `xcode-select --install`
   verwenden: Dieser Befehl installiert nur die fuer XDF-Recording
   unzureichenden standalone Command Line Tools. Die aktuelle App-Store-Version
   kann ausserdem ein neueres macOS und Apple Silicon verlangen.
6. Zum Terminal zurueckkehren und Xcode parallel zu vorhandenen Versionen
   installieren. Dieser Block ueberschreibt niemals
   `/Applications/Xcode-26.3.app`:

```bash
if [[ -e /Applications/Xcode-26.3.app ]]; then
  echo "Xcode 26.3 ist bereits installiert und bleibt unveraendert."
else
  xcode_stage="$(mktemp -d "${TMPDIR:-/tmp}/study-runner-xcode.XXXXXX")" &&
  (
    cd "$xcode_stage" &&
    xip --expand "$HOME/Downloads/Xcode_26.3.xip" &&
    sudo mv Xcode.app /Applications/Xcode-26.3.app
  ) && /bin/rmdir "$xcode_stage"
fi
```

7. Diese Xcode-Installation initialisieren und pruefen, ohne die globale
   `xcode-select`-Einstellung zu veraendern:

```bash
export DEVELOPER_DIR=/Applications/Xcode-26.3.app/Contents/Developer
sudo env DEVELOPER_DIR="$DEVELOPER_DIR" /usr/bin/xcodebuild -runFirstLaunch
/usr/bin/xcodebuild -version
/usr/bin/xcrun --sdk macosx --find clang++
/usr/bin/xcrun --sdk macosx --show-sdk-path
```

8. Study Runner installieren:

```bash
bash tools/install-macos.sh --install-system-dependencies
```

Homebrew ist nicht erforderlich. Xcode wird nur fuer die Installation mit XDF-
Recording gebraucht und vom Skript nur fuer den laufenden Prozess ausgewaehlt;
die globale `xcode-select`-Einstellung bleibt unveraendert. Falls ein natives
Python 3.12 fehlt, laedt das Skript den festgelegten Universal-Installer von
python.org herunter, prueft dessen SHA-256-Pruefsumme und
Apple-Installersignatur und fragt fuer die Installation nach dem
Administratorpasswort. CMake wird nur innerhalb von `.venv` installiert. Fuer
die erste Installation ist daher eine Internetverbindung erforderlich. Der
Befehl kann nach einem Abbruch oder Update sicher erneut ausgefuehrt werden.

Falls Xcode noch nicht initialisiert ist, Schritt 7 wiederholen und danach
denselben Installerbefehl erneut ausfuehren. Eine vorhandene gueltige `.venv`
bleibt erhalten und wird wiederverwendet; nach der Toolchain-Reparatur wird
hoechstens der erzeugte CMake-Cache des nativen Cores kontrolliert neu
aufgebaut.

Eine Installation ohne Recording-Core ist mit
`bash tools/install-macos.sh --skip-recording-core` auch ohne Xcode und CMake
moeglich. Studien, die XDF-Recording verlangen, bleiben dann gesperrt.

9. Warten, bis `Study Runner is ready` erscheint, und Study Runner starten:

```bash
bash tools/start-macos.sh
```

10. Auf die Admin-Seite warten. Falls sie nicht automatisch erscheint,
`https://localhost:3000/admin` im Browser oeffnen.

Auf Apple Silicon kann `camera_emotion` den lokalen DeepFace-Worker verwenden.
Fuer macOS Intel gibt es mit Python 3.12 aktuell keine passenden
TensorFlow/tf-keras-Wheels. Die Intel-Installation unterstuetzt Server und XDF-
Recording vollstaendig, fuer Kamera/Emotion muss aber `remote_worker` mit einem
anderen Analyse-Rechner konfiguriert werden.

### macOS: Desktop-Verknuepfung erstellen

1. Study Runner nach dem ersten Start samt Terminalfenster laufen lassen.
2. Auf der Admin-Seite **Einstellungen** oeffnen, unter **System** den Eintrag
   **Desktop-Shortcut erstellen** auswaehlen und **Verknuepfung anlegen**
   anklicken.
3. Auf die Erfolgsmeldung warten. Auf dem Desktop liegt jetzt
   `Study Runner.command`.
4. Im ersten Terminalfenster `Ctrl+C` druecken, um den Server zu beenden.

Dieselbe Verknuepfung funktioniert auf Mac Intel und Apple Silicon. Sie zeigt
auf den aktuellen Installationsordner. Nach einem Umzug oder einem neu
heruntergeladenen Release muss sie erneut erstellt werden.

### macOS: spaeter starten

Auf dem Desktop `Study Runner.command` doppelklicken. Beim ersten Mal eine
eventuelle Rueckfrage mit **Oeffnen** bestaetigen. Das Terminalfenster offen
lassen, solange Study Runner laeuft, und den Server dort mit `Ctrl+C` beenden.

Falls die Verknuepfung fehlt, Terminal wieder im Study-Runner-Ordner oeffnen
und ausfuehren:

```bash
bash tools/start-macos.sh
```

Die `.venv` muss auf keiner Plattform manuell aktiviert werden. Der Startbefehl
verwendet immer direkt den richtigen Python-Interpreter. Danach ist Admin hier:

```text
https://localhost:3000/admin
```

### Installation reparieren

Im bestehenden Ordner das passende Installationsskript noch einmal ohne
System-Schalter ausfuehren. Es aktualisiert die Python-Abhaengigkeiten und
verwendet einen bereits gueltigen XDF-Kern weiter:

```powershell
.\tools\install-windows.cmd
```

```bash
bash tools/install-macos.sh
```

Nur fuer eine Installation ohne Sensoraufzeichnung gibt es
`-SkipRecordingCore` beziehungsweise `--skip-recording-core`. Studien ohne XDF
laufen dann, Pflicht-Recording bleibt jedoch mit einem klaren Hinweis blockiert.

### Neues heruntergeladenes Release verwenden

Ein heruntergeladenes Archiv besitzt keine automatische Aktualisierung. Vor
dem Wechsel die lokalen Studien, Einstellungen und Ergebnisse aus dem alten
Ordner sichern. Den alten Ordner erst loeschen, wenn die Daten im neuen Release
vorhanden und geprueft sind. Danach die Installation und auf macOS auch die
Desktop-Verknuepfung fuer den neuen Ordner erneut ausfuehren.

Die Source-Installation braucht weder signierte App-Pakete noch Apple-
Notarisierung.

### Alternative fuer Git-Nutzer

Wer Study Runner mit `git pull` im selben Ordner aktualisieren moechte, kann
statt des Release-Archivs das Repository klonen:

```bash
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
```

Danach gelten dieselben Installations- und Startbefehle wie oben.
Zum Aktualisieren den Server beenden, im geklonten Ordner `git pull --ff-only`
ausfuehren und danach das Installationsskript ohne System-Schalter erneut
starten.

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
Modell verwendet. macOS Intel nutzt fuer die Analyse immer `remote_worker`.

WLAN- und LAN-Sensoren liefern LSL direkt. BLE uebertraegt selbst kein LSL:
Der lokale BLE-Adapter empfaengt die Pakete und stellt sie danach als LSL-Stream
bereit. Browserquellen benoetigen HTTPS, Heartbeat, Sequenznummer und Quellzeit.

Beim Klick auf Submit werden die Antworten zuerst lokal sicher geschrieben.
Danach sieht der Participant bereits die Abschlussseite. Im Admin-Fenster laufen
XDF-Abschluss, Quellenpruefung, Merge, Statistik, Notion und Nextcloud sichtbar
im Hintergrund weiter. Ein Fehler wird als `attention_required` angezeigt und
nie still als Erfolg behandelt.

## Neues Update veroeffentlichen

Das ist nicht Teil der normalen Bedienung. Ein Push auf `main` erzeugt noch
kein Update; ein oeffentlicher Release entsteht erst durch einen Release-Tag,
und der wird mit `.\release.ps1 patch` aus dem Hauptordner gesetzt.

Die vollstaendige Beschreibung -- Release-Dateien, Abnahme-Gates und was auf
Windows und macOS gruen sein muss -- steht in
[release-and-update.md](release-and-update.md).

## Update am Nutzer-Rechner

Der aktuelle Source-Server aktualisiert sich nicht selbst. Server stoppen und
im Projektordner ausfuehren:

```powershell
git pull --ff-only
.\tools\install-windows.cmd
.\tools\start-windows.cmd
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

Alles Weitere ist auf Englisch:

- `operator-guide.md`: taegliche Bedienung im Labor.
- `how-recording-quality-works.md`: was Luecken, Jitter und die
  Sitzungs-Zustaende bedeuten. Ohne Code geschrieben.
- `sensors-and-data.md`: was du einstellst und welche Dateien herauskommen.
- `release-and-update.md`: Source-Updates, Release-Dateien und Abnahme-Gates.
- `plugin-recording-architecture.md`: kompletter Plugin-, Worker-,
  Finalisierungs- und Recovery-Bauplan.
- `developer-guide.md`: Struktur und Regeln fuer Code-Aenderungen.
- `file-guide.md`: eine Zeile pro Quelldatei.

Eine Uebersicht, wer welches Dokument liest, steht in der
[README](../README.md#documentation).
