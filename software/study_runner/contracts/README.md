# Contracts

The shapes and rules that more than one part of Study Runner must agree on —
what a plugin manifest may contain, what a session's lifecycle states are,
what one line of a quality journal looks like. Nothing here touches a file,
the network, or Flask; every function takes plain data in and returns plain
data out, so the same rule can be checked from the host process, a plugin's
own subprocess, or a test, without any of them importing each other.

That last point is a real constraint, not a style preference: the host and a
plugin's `driver.py` subprocess are deliberately not allowed to import one
another's code (documented as "invariant #1" in the architecture doc), and
the recording worker process is similarly isolated from the host (invariant
#2). `contracts/` is the neutral ground both sides may depend on instead.

## Files

| File | What it does | In / Out | Used by |
|---|---|---|---|
| `manifest.py` | The plugin manifest contract itself — the biggest and most central file here. `validate_and_normalize_manifest()` takes a raw `manifest.json` and either rejects it with a specific, actionable error or returns its one stable, normalized shape. `validate_admin_action_payload()` checks a request body against one manifest-declared action's own schema. | a raw JSON manifest → a normalized manifest dict, or a `PluginManifestError` | Plugin discovery (`plugin_framework/plugin_catalog.py`), the sensor/clock host modules, and every plugin's own `driver.py` |
| `plugin_api.py` | The typed shape of a loaded plugin: `PluginContext` (what a plugin receives instead of importing Flask directly — its config, secrets, and a `secret()` lookup) and `Plugin` (which handlers it implements: start/stop, admin actions, card handling, and so on). | — | Nearly everything that touches a plugin (32 files) — this is the most-used file in the folder |
| `card_validation_primitives.py` | Small, generic input checks (`normalize_text`, `normalize_integer`, `normalize_boolean`, ...) that every card's own validation is built from, so "what counts as a valid number" is answered once. | raw form/JSON values → normalized values, or `CardValidationError` | Every card extension's own validation code (16 files) |
| `card_options.py` | The one shared rule for an options list, used by both the choice and ranking cards: at least one option, each one trimmed text. | a question dict → its normalized `options` list | `plugins/cards/choice`, `plugins/cards/ranking` |
| `participant_fields.py` | The fixed vocabulary of participant-identity fields (first name, age group, ...), their defaults, and the two fields' selectable options — one list instead of a copy in the identity card and a copy in whatever else asks. | — | the participant-ID card, its settings, and the demographics it can request |
| `stream_contract.py` | Turns one manifest-declared LSL stream into the flat text fields written into that stream's XDF header, so every recorded stream carries the same self-describing block instead of five hand-copied versions of it. | a manifest stream dict → header fields (and a helper to append them to a live `pylsl.StreamInfo`) | every LSL-producing adapter, `card_summary_service.py` (reads it back) |
| `session_lifecycle.py` | Names one overall state for a session (`IDLE` → ... → `SEALED`/`WITHDRAWN`/`FAILED`) derived from the three separate documents that already track recording, finalization, and quality — without replacing any of them. Also the one allowed-transitions table. | recording/finalization/marker documents → one lifecycle name | the session browser, the withdrawal workflow |
| `quality_journal.py` | What counts as a live recording-quality event (a gap, a timestamp regression, a clock jump, a filling buffer) and when a number becomes a journal line — computed while recording, so a session that never finishes still leaves a quality record. Pure and dependency-free so the detached recording-worker process can use it directly. | a stream's raw timestamps → quality/timing journal events | the recording worker's ingest loop, the host's recovery path |
| `recording_checkpoint.py` | How far a recording is confirmed to actually be on disk. A checkpoint is only trustworthy because it is written *after* the data it describes and fsynced; anything past the last surviving checkpoint after a crash is the honestly-named "unconfirmed tail" rather than an assumed loss of zero. | write progress → checkpoint records; a checkpoint journal → the last confirmed position | the recording worker (writes), the host's crash-recovery path (reads) |
| `__init__.py` | One line: what this package is for. No shared code of its own. | — | — |

## Plugin entry points

Supported manifests use `runtime.entrypoint` to select the process driver.
Unknown top-level fields are ignored during normalization and never select
executable code.
