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

1. Auf der Release-Seite unter **Assets** `study-runner-source.zip`
   herunterladen (nicht die automatischen "Source code"-Links) und entpacken.
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

1. Auf der Release-Seite unter **Assets** `study-runner-source.tar.gz`
   herunterladen (nicht die automatischen "Source code"-Links). Falls der Browser es nicht
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

Ab 1.2.0 aktualisiert sich ein entpacktes Release-Archiv selbst (siehe
"Update am Nutzer-Rechner" unten). Nur wer von 1.1.x oder aelter kommt, zieht
einmal von Hand in einen neuen Ordner um -- die Schritte stehen ebenfalls dort.

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

- BrainBit EEG ueber Bluetooth/NeuroSDK (`brainbit`).
- AM Hub (`am_hub`): Praesenz, Position, Bewegung, Herz- und Atemfrequenz,
  Ventilzustand und Verbindungsqualitaet vom Parasite AM Hub.
- MR60 Radar ueber ESP32-C6 BLE-Firmware (`mini_radar`).
- Kamera und Emotion gemeinsam als Plugin `camera_emotion`; lokaler oder
  entfernter Analyse-Worker sind nur Betriebsarten dieses Plugins.
- LSL als gemeinsamer Datenweg und der Python-Recording-Worker mit kleinem
  XDF-Kern fuer synchronisierte Rohdaten.
- Notion Upload fuer kompakte Zusammenfassungen, wenn ein API-Key gesetzt ist.
- Nextcloud Upload der fertigen Session-Ordner.
- OSC fuer Live-Signale an TouchDesigner und aehnliche Programme.

Wichtig: Kamera-Emotion streamt Livebilder ins Dashboard, sobald Camera Emotion
effektiv aktiv ist, die Tablet-Seite offen ist und die Kamera erlaubt wurde.
Vor dem Studienstart werden diese Bilder nur fuer den Live-Monitor genutzt.
Gespeichert wird erst nach gueltiger Participant ID und Studienstart.

Die geladene Studie bestimmt, welche Sensoren laufen: Beim Laden starten die
Sensoren, die sie braucht, alle anderen werden gestoppt. Eine Studie ohne
Sensorik hat keine aktiven Sensoren. Der An/Aus-Schalter im Dashboard weicht
davon voruebergehend ab, bis die Studie neu geladen wird. Mit `Auf Studie
zuruecksetzen` faellt alles wieder auf die Studienwerte zurueck.

### Ablauf im Labor

1. Studie laden, Tablet verbinden (es zeigt Studie und Logo).
2. Im Dashboard die Sensoren vorbereiten. Jede Sensor-Kachel zeigt dieselbe
   Kopfzeile: Status (z. B. "Verbunden – Elektrodenkontakt gut ·
   Kalibrierung fertig"), ein gruenes **Bereit**, den Schalter und die
   Schritte. Der hervorgehobene Knopf ist immer der naechste Schritt.
   - BrainBit ohne bekanntes Band: **Suchen**. Genau ein gefundenes Band wird
     automatisch verbunden; bei mehreren das Band in der Liste waehlen, das
     verbindet sofort. Ein bekanntes Band verbindet sich spaeter von selbst.
   - Der Elektrodenkontakt wird beim Verbinden automatisch gemessen.
     **Kontakt messen** wiederholt das ohne Neuverbindung.
   - **Initialisieren** kalibriert fuer die Person, die das Band traegt
     (etwa 6 s ruhig sitzen, Augen offen).
   Die Daten laufen schon live in die Vorschau, werden aber noch nicht
   aufgenommen.
3. **Studie starten** in der Studienleiste des Dashboards (oder im Hub). Der
   Knopf wird hervorgehoben, sobald alle noetigen Sensoren bereit sind und das
   Tablet verbunden ist; sonst nennt die Leiste, was noch fehlt.
4. Das Tablet zeigt die Infoseite (falls vorhanden) und dann die Participant
   ID. Mit dem Absenden der ID beginnt die Aufnahme. Dabei wird nichts neu
   verbunden oder initialisiert.
5. Nach der Session laufen die Sensoren weiter. BrainBit verlangt fuer die
   naechste Person wieder Kontakt messen und Initialisieren.

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

Findet die Pruefung nur Qualitaetswarnungen (etwa einen Stream, der wenige
Millisekunden zu spaet beginnt), sind die Daten nutzbar: **Mit Warnung
fortsetzen** mit Begruendung, dann entstehen Merge, Statistik und CSV trotzdem
und die Session ist als eingeschraenkt markiert. Nur bei blockierenden
Problemen (etwa einer unlesbaren Datei) wird die Session mit den Rohdaten
abgeschlossen, wie sie sind.

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
- Was du selbst in die Programmordner gelegt hast (ein zusaetzliches Plugin, eine
  Schrift unter `apps/ui/fonts/`), wird mit ersetzt und liegt danach im
  Backup-Ordner -- von dort zurueckkopieren.

**Wenn etwas schiefgeht:**

- Download, Pruefung oder Entpacken scheitert: nichts wurde veraendert. Im
  Update-Bereich erneut "Pruefen".
- Eine Datei ist beim Austausch gesperrt (ein Programm hat eine Datei im
  Study-Runner-Ordner offen, ein Cloud-Sync-Programm): die alten Dateien werden
  zurueckgelegt und die alte Version startet wieder.
- Wurde der Server waehrend des Downloads beendet, zeigt der Update-Bereich
  beim naechsten Start "unterbrochen" -- einfach erneut pruefen.
- Protokoll: `software/updates/update-helper.log`. Die alte Version liegt in
  `.tools/update-backup/<alte-Version>-<Zeit>/`. Zurueck von Hand: Study Runner
  beenden, die aktuellen Programmordner beiseitelegen, die Ordner aus dem
  Backup in den Installationsordner verschieben, Installationsskript ausfuehren.

**Windows und lange Pfade:** Ab 1.3.2 braucht das Update die
Windows-Einstellung "lange Pfade" nicht mehr. Die Updater bis 1.3.1 schon:
bleibt der Update-Bereich einer 1.2.x/1.3.x-Installation unter Windows bei
"Verifying" haengen oder meldet einen Entpack-Fehler, ist nichts veraendert --
dann einmal den Weg unten nehmen (ein kurzer Ordner wie `C:\StudyRunner`
vermeidet das Problem ganz).

**Per Terminal:** Study Runner mit Ctrl+C beenden, dann im Programmordner:

```bash
bash tools/update-macos.sh           # Mac
bash tools/update-macos.sh --check   # nur pruefen
```

```powershell
.\tools\update-windows.cmd
```

Danach wie gewohnt starten. Ctrl+C waehrend der Installation stellt die alte
Version wieder her.

**Einmalig, wenn das Update "not a git clone" oder "neither a release archive
nor a git clone" meldet:** Die installierte Version ist aelter als 1.5.5 und
war entweder 1.1.x oder aelter oder stammt aus GitHubs automatischem "Source
code"-Archiv. Ihr eigener Code kann diesen Ordner nicht aktualisieren. Ab 1.5.5
klappt das Update auch fuer solche Ordner.

1. Study Runner beenden.
2. Aus dem neuesten Release unter **Assets** `study-runner-source.zip`
   (Windows) bzw. `study-runner-source.tar.gz` (Mac) laden.
3. In einen neuen Ordner entpacken (unter Windows am besten ein kurzer Pfad).
4. `software/study_content` und `software/saved_results` aus dem alten Ordner in
   den neuen kopieren und die dortigen Ordner ersetzen. Optional auch
   `software/runtime` (Plugin-Logs, Emotionsmodell) und selbst hinzugefuegte
   Plugins oder Schriften. Liegt der Datenordner auf einer externen Festplatte,
   ihn stattdessen in den Einstellungen neu verknuepfen.
5. `.venv` und `.tools` nicht kopieren -- sie zeigen auf den alten Ordner.
6. `bash tools/install-macos.sh` bzw. `.\tools\install-windows.cmd` ausfuehren
   (braucht Internet, einige Minuten) und starten. Auf dem Mac die
   Desktop-Verknuepfung im Dashboard neu anlegen.
7. Den alten Ordner erst loeschen, wenn Studien und Ergebnisse im neuen Ordner
   geprueft sind.

`git pull` funktioniert in einem entpackten Archiv nicht. Git-Installationen:
`git pull --ff-only`, dann das Installationsskript. Danach aktualisiert sich der
neue Ordner selbst.

**Studienordner:** Ab 1.2.0 sind die Dateien in `software/study_content/studies`
Zip-Pakete mit ihren Bildern (Endung weiterhin `.study-runner`). Alte
JSON-Dateien werden beim ersten Start einmalig umgewandelt; die Originale
liegen in `studies/_backup-json/`.

### Datenordner (z. B. externe Festplatte)

Studien, Ergebnisse, Einstellungen, Zugangsdaten, Logos und das
iPad-Zertifikat liegen zusammen in einem Datenordner. Standard ist der
Programmordner. Unter **Einstellungen > Datenordner** laesst er sich woanders
hinlegen, zum Beispiel auf eine externe Festplatte:

- **Leerer Ordner:** wird eingerichtet wie nach einer Neuinstallation. Das
  bedeutet auch ein neues iPad-Zertifikat, dem die Tablets einmal vertrauen
  muessen.
- **"Aktuelle Daten mitnehmen":** kopiert vorher alles in den neuen Ordner. Die
  alte Kopie bleibt liegen, bis du sie selbst loeschst.
- **Vorhandener Study-Runner-Datenordner:** wird einfach verknuepft.
- Study Runner startet danach neu. Waehrend einer Session oder eines laufenden
  Studienlaufs geht das nicht.
- **Nach einer Neuinstallation** zeigt Study Runner den zuletzt genutzten
  Datenordner an ("Verknuepfen und neu starten") -- danach ist alles wieder da.
- **Festplatte nicht angeschlossen:** Study Runner startet nicht und sagt im
  Startfenster, welcher Ordner fehlt. Festplatte anschliessen und neu starten.
  Wer stattdessen wieder den Programmordner nutzen will, loescht die Datei
  `data-folder.json` im Installationsordner.
- Updates lassen die Einstellung und den Datenordner unangetastet.

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
