# Study Runner 1.0 – Architekturentscheidung

Nachfolger von `MRG_Recorder_Core_Entscheidung.md`. Bezugspunkt ist der
Ist-Stand 0.7.0. Dieses Dokument beschreibt das Zielbild für den Major
Release 1.0 und benennt ausdrücklich, was übernommen, was ergänzt und was
entfernt wird.

## 1. Kurzentscheidung

**XDF bleibt das zentrale Aufzeichnungsformat, LSL die gemeinsame
Akquisitionsgrenze.** Geräte-Module liefern ausschließlich Daten und
Metadaten. Ein eigenständiger Recorder Core übernimmt Aufzeichnung,
Integrität, Timing, Qualitätskontrolle, Recovery, Finalisierung und Merge.

Ein Core bedeutet eine zentrale Verantwortung, nicht einen einzigen
Writer-Thread und nicht eine einzige Live-Datei.

1.0 erfindet weder LSL noch XDF noch ein Speicher- oder Plugin-Framework neu.
1.0 zieht die Grenzen zwischen den vorhandenen Teilen so, dass sie prüfbar
sind, und entfernt die Altlasten, die sich bis 0.7 angesammelt haben.

## 2. Was aus 0.7 unverändert übernommen wird

Diese Substanz ist gebaut, getestet und darf im Refactor nicht verloren
gehen. Sie ist Teil der Architektur, nicht Implementierungsdetail.

| Baustein | Garantie |
|---|---|
| Native C-ABI um den gepinnten LabRecorder-XDFWriter | kanonische XDF-Kodierung, exklusive Dateierzeugung, durabler Flush, geprüfter Chunk-Append beim Merge |
| Fail-closed Core-Locator | fehlender, fremder, veränderter oder ABI-inkompatibler Core blockiert Aufnahme, statt still zu degradieren |
| Segmentierung | Boundary etwa alle 10 s, durabler Flush mindestens alle 5 s, ein Crash setzt nie ein Segment fort |
| Zweistufiger Merge | deterministische Container-IDs, unveränderte Sample- und Clock-Payloads, kein Resampling, kein Dejitter |
| Merge-Validierung mit gepinntem PyXDF im raw mode | Metadaten, Samplezahl, vollständiger Timestamp-Verlauf, Clock-Offset-Chunks, normalisierter Daten-Hash; Abweichung ist Fehler, nie Warnung |
| Append-only Session-Journal mit fsync auf Datei und Parent-Verzeichnis | jede Transition ist vor der Quittung persistent |
| Lease, Generation-Fencing, idempotente `command_id` | Worker-Reattach nach Server-Neustart ohne zweite Wahrheit |
| Slowest-Grid Backup-Projektion | QC-Artefakt mit `valid`, `sample_age_ms`, Status `missing/valid/stale/degraded`, nie stiller Carry-Forward |
| Journalisierte Finalisierung in neun Schritten | deterministischer Replay über Plugin-Upgrades hinweg |
| Prozessisolierte Plugins über `study-runner-stdio/v1` | der Kernprozess importiert nie Plugin-Code |

PyXDF bleibt Validator und Importer, niemals Writer.

## 3. Zielarchitektur

Zwei Kerne und drei Adapter. Frühere Fassungen sprachen von fünf
gleichwertigen Hauptmodulen; das war ungenau, weil Server, UI und CLI keine
eigene Fachlogik besitzen dürfen.

```text
┌──────────┐   ┌──────────┐
│ UI       │   │ CLI      │      Bedienwege, keine Fachlogik
└────┬─────┘   └────┬─────┘
     │ HTTPS/SSE    │ lokale Command-API
     ▼              ▼
┌───────────────────────────┐
│ Server                    │    Transport, TLS, Auth, Auslieferung
└─────────────┬─────────────┘
              ▼
┌───────────────────────────┐
│ RuntimeCore               │    Studien, Sessions, Cards, Commands,
│                           │    Plugin-Host
└──────┬─────────────┬──────┘
       ▼             ▼
┌────────────┐  ┌───────────────────────────┐
│ Extensions │─▶│ DataCore / Recorder Core  │
│ Sensor     │  │ LSL · XDF · Timing · QC   │
│ Card       │  │ Merge · Recovery · Replay │
│ Destination│  └───────────────────────────┘
│ Output     │
└────────────┘
```

### Paketstruktur 1.0

```text
software/
├── apps/
│   ├── server/            Flask-Adapter, TLS, SSE, UI-Auslieferung
│   ├── cli/               mrg-Kommandos
│   └── ui/                gebautes Frontend
├── packages/
│   ├── contracts/         von allen importierbar, importiert nichts zurück
│   ├── runtime_core/      studies/ · sessions/ · results/
│   ├── data_core/         contract/ · host/ · worker/
│   └── plugin_framework/  Discovery, Manifestprüfung, Prozess-Host
├── plugins/            sensors/ cards/ destinations/ outputs/
└── study_content/
```

### Prozessgrenze im DataCore

Die dreifache Benennung "recording" in 0.7 verdeckt die eigentliche Achse.
Maßgeblich ist, in welchem Prozess Code läuft:

- `data_core/contract/` wird von beiden Prozessen importiert: Worker-Protokoll,
  Artefaktlayout, Fehlertypen, XDF-Validierungsverträge, Core-Probe.
- `data_core/host/` läuft im Serverprozess: Coordinator, Launcher,
  Orchestrierung, Quality, Recording-Vertrag, Lease.
- `data_core/worker/` läuft im abgesetzten Prozess: Loopback-Host,
  LSL-Ingest, Kommandobehandlung, ctypes-Bindung an den nativen Core.

`host` und `worker` importieren beide `contract` und niemals einander.

## 4. Verantwortungsgrenzen

### Geräte-Module

Module verantworten Verbindung zum SDK, Samples und Source-Timestamps,
Kanaldefinitionen mit Einheiten und Samplingrate, Geräte-ID und
Firmware-/SDK-Version, bekannte Timing-Eigenschaften, optionale
gerätespezifische Qualitätswerte sowie Verbindungs- und Fehlerstatus.

Module schreiben keine Session-Dateien, führen keinen Merge aus, verändern
keine Timestamps und lesen oder schreiben keine Studienkonfiguration.

### Recorder Core

1. Registrierung und Prüfung erwarteter LSL-Streams
2. segmentierte, crash-sichere XDF-Aufzeichnung
3. Session-Lifecycle und maßgebliches Append-only Journal
4. Timing-Beobachtungen und ehrliche Unsicherheitsangaben
5. technische Qualitätskontrolle und Fehlerprotokollierung
6. Recovery nach Prozess-, Geräte- oder Stromausfall
7. deterministische Validierung, Finalisierung und Merge
8. Status- und Steuerungs-API für RuntimeCore, UI und CLI

## 5. Stream-Vertrag

Jeder Stream wird vor Aufnahmebeginn eindeutig beschrieben:

```text
stream_id · module_id · device_serial · schema_version
channels · units · nominal_rate · timestamp_origin
required|optional · timing_profile
```

Der Vertrag wird beim Start eingefroren und mit der Session persistiert.
Ändern sich Gerät, Schema oder Kanäle, entsteht eine neue Stream-Version.
Eine Abweichung vom eingefrorenen Vertrag während einer laufenden Session
ist ein Qualitätsereignis und kann nie stillschweigend passieren.

Neu in 1.0: Der Vertrag wird zusätzlich in den XDF-Streamheader geschrieben,
damit eine Session ohne Study Runner interpretierbar bleibt.

## 6. Timing

Rohdaten behalten immer ihren ursprünglichen `source_time`. Zusätzlich
speichert der Core Empfangszeit, Clock-Observations und ein Timing-Profil:

```yaml
timestamp_origin: device | host | browser
capture_delay_ns:
  min: 12000000
  max: 18000000
  source: measured | datasheet | estimated | unknown
  reference: "loopback-2026-08-14, tools/measure_capture_delay.py"
```

Regeln:

- Eine Globalzeit wird erst beim Lesen oder Export berechnet, nie persistiert.
- Ist die Verzögerung unbekannt, bleibt die wissenschaftliche Zeitunsicherheit
  ausdrücklich unbekannt. `unknown` ist ein zulässiges und ehrliches Ergebnis.
- `source` ist Pflicht. Ohne Herkunft wird aus einer Schätzung nach zwei
  Jahren stillschweigend eine Tatsache.
- Intern wird ausschließlich die monotone Uhr für Dauern verwendet. Die
  Wandzeit erscheint nur als je ein UTC-Anker bei Sessionstart und -ende.
- Ein Sprung der Systemuhr während der Aufnahme, etwa durch NTP-Korrektur
  oder Zeitumstellung, wird erkannt und als Qualitätsereignis protokolliert.
- LSL-Clock-Offsets werden weiterhin als XDF-Clock-Offset-Chunks geschrieben,
  nicht in eine parallele Eigenstruktur.

Der Core garantiert keine pauschale Millisekundengenauigkeit, sondern liefert
eine benannte Unsicherheit mit benannter Herkunft.

## 7. Marker und Ereignisse

Marker und Antworten sind das teuerste Datum der Studie und dürfen nicht
allein an einem Transportweg hängen.

1.0 schreibt sie doppelt: der RuntimeCore kennt jedes Stimulus- und
Antwortereignis ohnehin und persistiert es synchron im Session-Journal;
zusätzlich läuft es über den LSL-Markerstream in die XDF-Aufnahme. Beide
Pfade tragen dieselbe `event_id`. Bei der Finalisierung werden sie
dedupliziert; eine Abweichung zwischen Journal und XDF ist ein
Qualitätsereignis.

Damit ist der LSL-Markerpfad Komfort und Zeitbezug, nicht die alleinige
Wahrheitsquelle.

## 8. Aufzeichnungsmodell und Session-Layout

```text
LSL Stream
→ begrenzte Stream-Queue
→ eigener Core-Writer
→ XDF-Segment
→ durabler Flush
→ Commit im Journal
```

Getrennte Queues und Writer verhindern, dass ein langsamer Stream alle
anderen blockiert. Ausschließlich der Core besitzt Schreibzugriff auf die
Session.

Die Commit-Invariante wird ausformuliert, nicht angedeutet: Daten
schreiben, durabel flushen, danach Journalzeile anhängen und fsyncen,
bei Dateianlage zusätzlich das Verzeichnis fsyncen. Beim Wiederanlauf ist
das Journal maßgeblich; alles hinter dem letzten Commit gilt als nicht
aufgezeichnet und wird als Lücke protokolliert. Der maximale Verlust bei
Stromausfall ist damit durch das Flush-Intervall begrenzt und wird in der
Betriebsdokumentation als Zahl genannt.

```text
saved_results/<study>/participants/<participant>/sessions/<UTC>__<session-id>/
├── submission.json
├── result.json
├── card-summary.json
├── manifest.json
├── checksums.sha256
├── stream-contracts.json        neu
├── timing.jsonl                 neu
├── quality.jsonl                neu
├── finalization-state.json
├── logs/finalization.jsonl
├── raw/plugins/<plugin>/part-0001.xdf
├── raw/backup/slowest-grid_<rate>hz.xdf
├── derived/session.xdf
└── COMPLETE.json | ATTENTION_REQUIRED.json
```

Die validierten Rohsegmente sind die Quelle. `derived/session.xdf` ist ein
reproduzierbarer, abgeleiteter Export.

## 9. Qualität und Fehlerregeln

Der Core überwacht objektive technische Kriterien: effektive Samplingrate,
Lücken und Drops, Timestamp-Rücksprünge, Jitter und Clock-Drift,
Stream-Ausfälle und Reconnects, Queue-Auslastung, freien Speicher sowie
bekannte oder unbekannte Zeitunsicherheit. Gerätespezifische Werte wie
EEG-Impedanz liefert das jeweilige Modul. Schwellenwerte stehen in einem
versionierten Qualitätsprofil.

Neu in 1.0:

- Qualität entsteht **während** der Aufnahme in `quality.jsonl`, nicht erst
  bei der Finalisierung. Eine abgebrochene Session hinterlässt sonst kein QC.
- Preflight prüft zusätzlich den freien Speicher gegen die geplante
  Sessiondauer und die gemessene Schreibrate und verweigert den Start, wenn
  die Reserve nicht reicht.
- Preflight prüft die Systemuhr auf Plausibilität und einen laufenden
  Zeitdienst.

Es gilt weiterhin: kein stiller Datenverlust; Marker werden priorisiert,
aber nicht unrealistisch garantiert; bei erschöpften Reserven kontrollierter
Abbruch; jeder Drop, Reconnect und manuelle Override wird persistiert.

## 10. Lifecycle

Sessionzustand:

```text
IDLE → PREFLIGHT → RECORDING → FINALIZING → SEALED
                                   ↘ WITHDRAWN
                                   ↘ FAILED
```

`WITHDRAWN` ist neu und deckt den Fall ab, dass eine teilnehmende Person die
Einwilligung während oder nach der Sitzung zurückzieht. Der Zustand löst die
dokumentierte Löschung der Sessiondaten aus und ist kein technischer Fehler.
Ohne diesen Zustand landet ein ethisch relevanter Vorgang in `FAILED`.

`SEALED` ist nur zulässig, wenn Segmente lesbar sind, Checksums stimmen,
Streamfooter mit den tatsächlich lesbaren Samplezahlen übereinstimmen und
der Merge Payloads sowie Source-Timestamps exakt erhält.

Der Finalisierungsauftrag bleibt getrennt davon in seinen bewährten
Zuständen `queued`, `running`, `attention_required`, `completed`,
`completed_degraded`.

## 11. Extensions

Vier Typen mit getrennten Verträgen und einer kleinen gemeinsamen Identität
aus `extension_type`, `api_version`, `id` und `version`.

| Typ | Liefert |
|---|---|
| Sensor | Geräteanbindung, Status, LSL-Streams, Timing-Metadaten |
| Card | Config- und Answer-Schema, Defaults, Renderer, Editor, Styles |
| Destination | Veröffentlichung bereits versiegelter Session-Artefakte |
| Output | Empfang semantischer Study-Events, etwa OSC |

Verbindliche Grenze: Eine Extension importiert ausschließlich aus
`contracts/`. Kein Import aus `runtime_core`, `data_core` oder
Server-Services. Was eine Extension braucht, kommt über ihr Context-Objekt
oder ihr Payload, nicht über einen Import. Eine Destination liest oder
schreibt insbesondere keine Studienkonfiguration.

Das Plugin SDK ist kein Hauptmodul und kein laufender Prozess. Für 1.0
schrumpft es auf das, was ohne externe Entwickler tatsächlich Wert hat:
versionierte JSON-Schemas, ein Validator, eine Fake-Runtime, eine
synthetische LSL-Quelle und je ein Template pro Typ. Sprachhilfspakete für
Python und JavaScript sind optional; maßgeblich ist immer der dokumentierte
Vertrag, nicht eine Bibliothek.

## 12. CLI

Die CLI ist gleichwertiger Bedienweg, kein Entwicklerwerkzeug.

```bash
mrg status
mrg study list | validate | start | stop
mrg plugin list | status <key> | restart <key>
mrg logs --follow
mrg recording inspect | recover | verify | seal <session>
mrg extension create | validate | test
```

Im Normalbetrieb nutzt die CLI dieselbe lokale Command-API wie die UI, damit
Locks, Berechtigungen, Zustandsprüfungen und Journale identisch gelten.

Im Maintenance-Betrieb, wenn Server oder UI nicht starten, darf sie definierte
Offline-Funktionen ausführen: Konfiguration und Study-Dateien validieren,
Logs und Sessionzustände lesen, XDF-Segmente prüfen, Recovery analysieren und
ausführen, Manifest und Checksums verifizieren, Server oder DataCore
kontrolliert starten. Schreibende Offline-Aktionen erfordern einen exklusiven
Maintenance-Lock; bei laufender Runtime verweigert die CLI direkte Änderungen.

## 13. Was 1.0 entfernt

Ein Major Release ist der einzige zulässige Zeitpunkt dafür. Jede Entfernung
bekommt einen Manifest-Versionssprung und einen Migrationsschritt beim Laden,
danach fällt der Code raus.

- der Kompatibilitätspfad, der Plugins mit älterer `api_version` direkt in den
  Hostprozess importiert
- das Manifestfeld `entry_point`, in der Doku bereits als funktionslos
  bezeichnet
- die flachen Aliasfelder unter `upload_destination.legacy`
- die Sonderbehandlung alter flacher Result-Ordner
- Aufwärts-Importe aus Extensions und Plugin-Framework in Server-Services
- die gegenseitige Abhängigkeit zwischen Host- und Worker-Seite des DataCore
- Zusammenlegung der Capabilities `readiness`, `runtime_control` und `health`
  zu einem Lebenszyklusvertrag

## 14. Invarianten

Diese Regeln sind mechanisch geprüft, nicht dokumentiert und gehofft. Jede
bekommt einen Test, der vor dem Umbau rot ist.

1. `data_core/host` und `data_core/worker` importieren einander nicht.
2. Kein Modul unter `plugins/` oder `plugin_framework/` importiert
   `runtime_core`, `data_core` oder Server-Services.
3. `contracts/` importiert nichts aus dem Rest der Anwendung.
4. Kein Kernmodul nennt einen Plugin-Schlüssel.
5. Nur der DataCore schreibt XDF-Bytes.
6. Kein persistiertes Feld enthält eine berechnete Globalzeit.
7. Jede Session mit `SEALED` besitzt vollständige Checksums und eine
   bestandene Merge-Validierung.

Zusätzlich läuft in der CI eine Strukturmessung mit vier Zahlen:
paketübergreifende Importkanten, Zyklen, Zeilen pro Paket, größte Datei.
Ein Schwellwert blockiert Regressionen.

## 15. Nicht Teil von 1.0

Eigene Geräte-SDKs, In-Process-Plugin-Runtime, WASM, Policy Engine, frei
programmierbare Processing-Pipeline, Cloud-Plattform, KI-basierte
Qualitätsbewertung, Video- und Audio-Media-Plane, direkte BIDS- oder
NWB-Aufzeichnung, eigene Kryptographie. SHA-256-Checksums bleiben, weil sie
Integritätsnachweis und keine Kryptographie sind.

## 16. Offene Entscheidungen

Diese zwei Punkte müssen vor dem 1.0-Tag entschieden sein, weil sie
rückwirkend teuer werden.

**Lizenz.** Das Repository ist öffentlich, die aktuelle `LICENSE` ist
proprietär mit vollständigem Rechtevorbehalt, das erklärte Ziel ist Open
Source, und das Projekt läuft unter EFRE-Förderung. Der jetzige Zustand ist
die ungünstigste Kombination: sichtbar, aber nicht nutzbar. Zu klären ist
außerdem, ob die Förderzusage eine Lizenzvorgabe enthält.

**Plattformmatrix.** Kanonische Aufnahme läuft auf Windows x64 und macOS,
Linux ist bewusst fail-closed. Für 1.0 ist zu entscheiden, ob das so bleibt.
Die Konsequenz ist heute, dass der günstige CI-Läufer den kritischsten Pfad
nicht ausführen kann und die eigentliche Absicherung an Hardware-Smoketests
als Release-Gate hängt.

## 17. Abnahme für 1.0

- alle Invarianten aus Abschnitt 14 sind als Test grün
- Preflight scheitert nachweislich bei zu wenig Speicher, fehlendem
  Pflichtstream und unplausibler Systemuhr
- eine Session mit gezogenem Stecker endet mit lesbaren Segmenten, korrektem
  Journalstand und protokollierter Lücke
- Marker aus Journal und XDF stimmen in einer synthetischen Session überein
- jede Sensor-Extension liefert ein `capture_delay_ns` mit `source`, notfalls
  `unknown`
- ein Widerruf führt über `WITHDRAWN` zur dokumentierten Löschung
- die Strukturmessung liegt unter den gesetzten Schwellwerten
- Hardware-Smoketest mit BrainBit, MR60, Tablet und Kamera bestanden

## 18. Positionierung

Der Study Runner ist ein study-aware LabRecorder: zentral kontrolliert,
intern fehlertolerant, timing-ehrlich und reproduzierbar. 1.0 macht diese
Eigenschaften prüfbar statt behauptet.
