# Notion upload plugin

Publishes finalized result and card-summary JSON to a Notion database, as an
optional per-study upload destination. Like the Nextcloud plugin, it only
runs after a session has finalized — never during acquisition.

## Architecture

Study Runner plugins are API v4: the core process supervises this plugin as
a subprocess and never imports its Python modules directly (see
`docs/file-guide.md`). This folder follows that same shape:

- `driver.py` — the only executable entry point. A one-line wrapper that
  calls `run_plugin_driver("notion")`; the core never imports anything else
  in this folder.
- `plugin.py` — the plugin logic running inside the supervised subprocess:
  status, publish, and the `test_connection` admin action.
- `adapter.py` — the Notion API integration itself: client construction and
  caching, database/data-source resolution, participant-metadata property
  mapping, and the actual page upload.
- `manifest.json` — declares this as an `upload_destination` capability
  plugin, its one credential (`STUDY_RUNNER_NOTION_API_KEY`), its study
  settings (`parent_page_id`, `database_id`), and its one admin action
  (`test_connection`). `sample_delivery: none` — like Nextcloud, it moves
  already-finalized files, not live samples.

## Where the code comes from

`adapter.py` is built directly on the **official `notion-client` Python
package** (`notion-client>=2.2.1`, pinned in `software/requirements.txt`) —
`Client(auth=api_key, timeout_ms=...)` from `notion_client`, called the same
way Notion's own SDK documentation shows. There is no bundled or copied
Notion example code: `get_client()`, `test_connection()`, and
`upload_study_result()` are original code that wraps the official client for
this project's specific needs (database/data-source discovery, participant
metadata property creation, error classification via `APIErrorCode`/
`APIResponseError`). Everything Notion-specific here is standard, documented
SDK usage, not adapted from a vendor example.

## Settings

- `parent_page_id` / `database_id` / `data_source_id` (study settings) —
  where results get written. The adapter can create the results database
  under the parent page on first use if it does not exist yet.
- `api_key` (per-study credential, env var `STUDY_RUNNER_NOTION_API_KEY` or
  the local secret store) — the Notion integration token. Never written into
  an exported study file.

The admin action **Test connection** (in the machine settings hub) exercises
the same `notion_client.Client` call a real publish would use, so an invalid
API key is caught before a study ever tries to upload.
