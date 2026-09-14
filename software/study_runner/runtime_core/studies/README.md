# Studies

A study, its run, and everything that run produces: the study configuration
itself, who's currently taking it, each answered card, the trial timing that
drives a stimulus, and reading a finished session back afterwards. The
biggest single folder in the backend (17 files, ~7100 lines) — most of what
happens between "operator presses Play" and "a session shows up in the
browser" lives here.

## Files

| File | What it does | In / Out |
|---|---|---|
| `migrate.py` | Applies one-way compatibility transforms for old stimulus timing, card types, participant-ID hints, and plugin settings/actions before current validation. | an older study document and manifest-driven normalizers to the current shape |
| `study_config_service.py` | Load, save, list, and delete studies on disk; migrates a few old config shapes (like a pre-card `stimulus_duration_ms` field) into the current one on the way in. | a `.study-runner`/JSON file ↔ the active study config |
| `study_plugin_config.py` | One-way migration from old, sensor-specific study fields (`send_signal`, `brainbit_to_lsl`, ...) into the current manifest-driven `study_settings.plugins` shape, so an old saved study still loads correctly without those names leaking back into new saves. | a study's raw settings → the current plugin-settings shape |
| `study_readiness_service.py` | Answers one question before the Play button may work: can this study actually deliver a complete result right now? Checks credentials, sensor availability, HTTPS requirements, and recording capacity — as a pure function over stored config, not live hardware state, so the answer can't change between the check and the click. | study config + hardware config → a list of specific blockers, or none |
| `study_client_service.py` | Tracks which tablet(s) currently have the study page open, from their heartbeat, so the admin dashboard knows a participant is there and can enforce "exactly one tablet at a time." | a heartbeat → live client status |
| `study_run_state_service.py` | The tiny persisted flag that gates the participant page until the operator presses Play — separate from the study config itself, which stays the source of truth for what's actually asked. | — → loaded/running/completed/stopped |
| `session_store.py` | The persistent registry of active study sessions, so a server restart doesn't forget one mid-study and turn a tablet's resume call into a 404. A session goes "stale" instead of silently resumable once its last activity is old enough. | a session's lifecycle events → its current state, resumable after a restart |
| `session_journal_service.py` | The durable, append-only audit trail behind `session_store.py` and `trial_event_service.py`: every acknowledged transition is fsynced to a session-scoped file before the small "current state" projection is updated, so crash recovery is deterministic. | a state transition → an appended, fsynced journal record |
| `trial_service.py` | Dispatches one trial event (start/stop/marker) to every relevant plugin and to the two mandatory internal recording sources (markers, clock diagnostics), and reports which ones actually succeeded. | a trial event → per-plugin dispatch outcomes |
| `trial_event_service.py` | The layer above that: gives every start/stop/marker command a stable, idempotent identity, persists the result before acknowledging it, and owns the server-side stop deadline so a late or dropped tablet callback can never leave a stimulus recording indefinitely. The biggest file in this folder. | a trial command + its timing → a durably recorded, idempotent outcome |
| `card_extension_bridge.py` | The runtime bridge to a card extension's own process for exactly the three things it must answer: its defaults, normalizing one question's config, validating one answer. Fails closed (as "unavailable", never "accepted") when the extension's process can't answer. | a question type + a request → the card extension's answer, or a clear unavailability error |
| `validation.py` | Validates and normalizes everything that reaches the server as a study config, a results submission, or a trial-timing payload — the single place both bounds are enforced, since none of it can be trusted just because it parsed as JSON. The second-biggest file here. | raw request payloads → normalized, safe-to-use data, or a `ValidationError` |
| `results_service.py` | Turns a validated submission into what actually gets saved: the result file itself, biosignal summaries from live sensor history, and any biosignal sidecar files a plugin's manifest asked for. | a validated submission → saved result files + summaries |
| `card_summary_service.py` | Computes deterministic per-question statistics from a validated, merged XDF file — knows nothing about Flask or live sensors, only a small `SampleReader` contract, so it's testable against synthetic data. | a merged XDF's streams → per-card statistics |
| `session_quality_summary.py` | Turns a session's raw `quality.jsonl` (gaps, clock jumps, an unconfirmed tail after a crash) into a handful of structured findings the admin session-detail view can render and translate — three health levels, not thirty numbers. | a session's quality journal → structured findings + one overall health level |
| `sessions_index_service.py` | Read-only browsing of already-finalized sessions and their recorded signal data for the admin session browser and timeline — no persistent index of its own, since finalized sessions are rare enough that rescanning the disk on every call is cheap and can never drift. | the data directory → the session list, one session's detail, or its signal samples |
| `recovery_service.py` | Finds sessions orphaned by a crash or a closed tab (from three kinds of leftover file: partial answers, periodic sensor flushes, or a raw recovery dump) and lets an operator finalize or discard each one — nothing is ever hard-deleted, just archived so the same scan won't surface it twice. | leftover session artifacts → recovery candidates, finalized or discarded |
| `__init__.py` | One-paragraph orientation for the whole folder. | — |

## How the pieces fit together

A session's path through this folder: `study_config_service.py` +
`study_readiness_service.py` decide whether a study can even start;
`session_store.py` (backed by `session_journal_service.py`) tracks it while
it's active; `trial_event_service.py` (built on `trial_service.py`) drives
every stimulus and marker durably; `card_extension_bridge.py` validates each
answer as it comes in through `validation.py`; and once submitted,
`results_service.py` and `card_summary_service.py` turn it into the saved
files and statistics that `sessions_index_service.py` later serves to the
admin session browser. `recovery_service.py` and `session_quality_summary.py`
are the two "after the fact" views: rescuing what a crash left behind, and
explaining what a finished recording's quality actually looked like.
