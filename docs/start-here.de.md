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

Mitgeliefert sind zwei Beispielstudien ("Example Basic Study", "Example
Sensors Study"); sie stehen in der Admin-Startseite unter den zuletzt
verwendeten Studien. Dazu kommt ein fertiges Beispielergebnis ("Demo Completed
Study") in der Liste der abgeschlossenen Studien.

Bequemer ist meistens die Admin-Oberflaeche:

```text
https://localhost:3000/admin
```

### Studien weitergeben (`.study-runner`-Dateien)

Mit **Herunterladen** neben einer Studie auf der Admin-Startseite speicherst du
eine `.study-runner`-Datei, mit **Studie importieren** liest du eine ein. Seit Version
1.1.1 ist diese Datei ein kleines Zip-Paket: Es enthaelt die Studie und alle
Bilder aus Info-Cards und Deckseite, jeweils mit Pruefsumme. Die Endung bleibt
`.study-runner`.

- **Alte Dateien funktionieren weiter:** Eine `.study-runner`- oder
  `.json`-Datei aus einer frueheren Version laesst sich unveraendert laden.
- **Umgekehrt nicht:** Study Runner 1.1.0 und aelter kann das neue Paket nicht
  lesen. Den anderen Rechner also zuerst aktualisieren.
- Die mitgelieferten Beispielstudien bleiben unveraendert nutzbar. Laedt man
  eine herunter, entsteht ein Paket im neuen Format.
- Zugangsdaten (Notion-Key, Nextcloud-Passwort) sind nie in der Datei. Auf dem
  anderen Rechner muessen sie neu eingetragen werden.

### Wenn ein Upload fehlschlaegt

Ein fehlgeschlagener Upload zu Notion oder Nextcloud blockiert keine Session.
Die Daten sind lokal gespeichert, die Session bekommt den Hinweis **Upload
fehlgeschlagen**. In der Session zeigt die Karte **Abschluss & Uploads** den
Grund; nach dem Beheben dort **Erneut versuchen** druecken. Hilfe zu jedem
Feld gibt der runde **(?)**-Knopf in den Plugin-Einstellungen.

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

### Was die Installation macht

Die Installation funktioniert auf Windows x64, Mac Intel und Mac Apple Silicon
gleich. Sie braucht beim ersten Mal Internet, aber **kein Administrator-
Passwort, kein Xcode, kein Visual Studio, kein WinGet und kein Homebrew**.
Alles, was sie herunterlaedt, bleibt im Study-Runner-Ordner:

- `.tools/`: das Hilfsprogramm uv und ein fest vorgegebenes Python 3.12
  (beide per SHA-256-Pruefsumme kontrolliert),
- `.venv/`: die Python-Pakete von Study Runner,
- `software/.build/xdf_core/`: der XDF-Aufnahmekern. Er wird fuer jedes Release
  auf GitHub gebaut und getestet, beim Installieren heruntergeladen, gegen die
  Pruefsumme aus dem Release-Archiv geprueft und auf dem eigenen Rechner noch
  einmal getestet.

Am System wird nichts veraendert. Wer den Ordner loescht, hat alles entfernt.
Der Installationsbefehl darf jederzeit erneut ausgefuehrt werden, zum Beispiel
nach einem Abbruch.

### Windows x64: erste Installation

1. `study-runner-source.zip` herunterladen und entpacken.
2. Den entpackten Study-Runner-Ordner nach **Dokumente** verschieben.
3. Diesen Ordner im Explorer oeffnen, in die Adresszeile klicken,
   `powershell` eingeben und Enter druecken.
4. Diesen Befehl in PowerShell ausfuehren:

```powershell
.\tools\install-windows.cmd
```

5. Warten, bis `Study Runner is ready` erscheint. Die erste Installation dauert
   einige Minuten.
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

### macOS 13 oder neuer, Intel oder Apple Silicon: erste Installation

1. `study-runner-source.tar.gz` herunterladen. Falls der Browser es nicht
   automatisch entpackt, die Datei im Finder doppelklicken.
2. Den entpackten Study-Runner-Ordner nach **Dokumente** verschieben.
3. Terminal oeffnen und `cd ` inklusive Leerzeichen eingeben. Den Ordner aus
   dem Finder in das Terminalfenster ziehen und Enter druecken.
4. Study Runner installieren:

```bash
bash tools/install-macos.sh
```

5. Warten, bis `Study Runner is ready` erscheint, und Study Runner starten:

```bash
bash tools/start-macos.sh
```

6. Auf die Admin-Seite warten. Falls sie nicht automatisch erscheint,
   `https://localhost:3000/admin` im Browser oeffnen.

Fuer jedes Release getestet wird macOS 15 auf Intel und Apple Silicon; der
Aufnahmekern selbst laeuft ab macOS 13.

Auf Apple Silicon kann `camera_emotion` den lokalen DeepFace-Worker verwenden.
Fuer macOS Intel gibt es mit Python 3.12 aktuell keine passenden
TensorFlow/tf-keras-Wheels. Die Intel-Installation unterstuetzt Server und XDF-
Recording vollstaendig, fuer Kamera/Emotion muss aber `remote_worker` mit einem
anderen Analyse-Rechner konfiguriert werden.

### Wenn die Installation mit einem Fehler abbricht

- **"could not download ..."**: Internetverbindung pruefen (haeufig: Proxy oder
  WLAN-Anmeldeseite der Uni) und denselben Befehl noch einmal ausfuehren.
- **"checksum" oder "could not be verified"**: Der Download ist beschaedigt oder
  passt nicht zu diesem Release. Befehl erneut ausfuehren; wenn der Fehler
  bleibt, das Release-Archiv neu herunterladen.
- **"... uses Python ..., move it aside"**: Ein alter `.venv`-Ordner aus einer
  frueheren Installationsmethode passt nicht. Den Ordner `.venv` umbenennen
  (zum Beispiel in `.venv-alt`) und die Installation erneut starten.
- Wer vorher nach der alten Anleitung Xcode 26.3 installiert hat, braucht es fuer
  Study Runner nicht mehr und kann es ueber den Finder aus `Programme` loeschen.

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

Im bestehenden Ordner einfach den Installationsbefehl noch einmal ausfuehren.
Er aktualisiert die Python-Abhaengigkeiten und verwendet einen bereits
geprueften XDF-Kern weiter:

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
vorhanden und geprueft sind. Danach im neuen Ordner die Installation und auf
macOS auch die Desktop-Verknuepfung erneut ausfuehren.

Die Installation braucht weder signierte App-Pakete noch Apple-Notarisierung
oder einen Apple-Developer-Account.

### Alternative fuer Git-Nutzer

Wer Study Runner mit `git pull` im selben Ordner aktualisieren moechte, kann
statt des Release-Archivs das Repository klonen:

```bash
git clone https://github.com/realfabianschmidt/MRG-StudyRunner.git
cd MRG-StudyRunner
```

Danach gelten dieselben Installations- und Startbefehle wie oben. Der Klon
laedt den XDF-Kern des neuesten Releases und verwendet ihn nur, wenn er zu den
Quellen im Klon passt. Wer den C++-Kern selbst veraendert, baut ihn mit
`--build-core-from-source` (Mac, braucht die Command Line Tools aus
`xcode-select --install`) beziehungsweise `-BuildCoreFromSource` (Windows,
braucht die Visual Studio C++ Build Tools).

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

Trockenlauf ohne Veraenderungen:

```powershell
.\release.ps1 patch -DryRun
```

Lokaler Vollcheck inklusive nativem XDF-Core:

```powershell
.\release.ps1 patch -FullChecks
```

Die vollstaendige Beschreibung -- Release-Dateien, Abnahme-Gates und was auf
Windows und macOS gruen sein muss -- steht in
[release-and-update.md](release-and-update.md).

## Update am Nutzer-Rechner

Ab Version 1.2.0 aktualisiert sich Study Runner selbst, egal ob aus dem
Release-Archiv oder per `git clone` installiert.

**Per Klick:** Admin-Seite, Bereich Update, "Jetzt aktualisieren". Der Dialog
zeigt, was dabei beendet wird; laeuft gerade eine Session, folgt eine zweite,
rote Bestaetigung. Dann:

- Eine laufende Session wird mit dem Grund "Software update" abgebrochen. Die
  bis dahin aufgezeichneten Daten bleiben erhalten.
- Ein laufender Studienlauf wird beendet, Sensoren werden gestoppt.
- Offene Abschluesse und Uploads laufen nach dem Neustart weiter.
- Das Archiv wird heruntergeladen und per SHA-256 geprueft. Die alten
  Programmdateien landen in `.tools/update-backup/`, die neue Version wird
  installiert, und Study Runner startet in einem neuen Fenster neu. Die Seite
  laedt sich danach selbst neu.
- Studien, Ergebnisse, Einstellungen, Zugangsdaten, Logos und Schriften
  (`software/study_content`, `software/saved_results`) werden nie angefasst.
- Scheitert die Installation, wird die alte Version wiederhergestellt und
  wieder gestartet.

**Per Terminal:** Study Runner mit Ctrl+C beenden, dann im Programmordner:

```bash
bash tools/update-macos.sh           # Mac
bash tools/update-macos.sh --check   # nur pruefen
```

```powershell
.\tools\update-windows.cmd
```

Danach wie gewohnt starten.

**Einmalig von 1.1.x auf 1.2.0:** Diese Versionen koennen sich aus dem Archiv
noch nicht selbst aktualisieren. Study Runner beenden, das neue Archiv in einen
neuen Ordner entpacken, `software/study_content` und `software/saved_results`
aus dem alten Ordner in den neuen kopieren (die dortigen Ordner ersetzen), dann
`bash tools/install-macos.sh` bzw. `.\tools\install-windows.cmd` ausfuehren und
starten. Git-Installationen: `git pull --ff-only`, dann das Installationsskript.

**Studienordner:** Ab 1.2.0 sind die Dateien in `software/study_content/studies`
Zip-Pakete mit ihren Bildern (Endung weiterhin `.study-runner`). Alte
JSON-Dateien werden beim ersten Start einmalig umgewandelt; die Originale
liegen in `studies/_backup-json/`. Wer die Daten dauerhaft ausserhalb des
Programmordners halten moechte, setzt `STUDY_RUNNER_DATA_DIR`.

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
