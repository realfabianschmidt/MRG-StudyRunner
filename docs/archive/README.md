# Archive

Plans, audits and implementation records that are no longer instructions.
They are kept because they explain *why* the code looks the way it does, and
several source comments cite them as provenance.

**Nothing in this folder is current.** Paths, package names, API versions and
open questions inside these files describe the repository as it was when they
were written — `extensions/` before it became `plugins/`, `web/` before
`apps/ui/`, numbered `docs/` files that no longer exist. Do not treat any of it
as a specification, and do not repair the stale paths: they are part of the
record. The active documentation is in the parent folder, listed in the
[root README](../../README.md).

## What is here

| File | What it recorded |
|---|---|
| `architecture-1.0-initial.md` | The 1.0 target architecture as originally proposed (German). The unchanged input to the rebuild. |
| `architecture-1.0-umbau.md` | The working document of the 1.0 rebuild: phase checklist, file ownership, decision log, and the verified traps. Cited by name from many source comments. |
| `architecture-1.0-handoff.md` | Continuation notes while two assistants worked on the rebuild in parallel. |
| `20260914_hotfix.md` | Implementation record of the README cleanup, the removal of the ten old HTTP routes, the `extensions/` to `plugins/` rename, and the card CSS work. |
| `roadmap-0.5.md` | The 0.5 planning document, baseline version 0.4.0. |
| `roadmap-0.5-t8-letsencrypt-alternative.md` | A publicly trusted HTTPS design that was considered and not pursued. |
| `03_plan_for_clearer_code.md` | An early folder-structure plan. |
| `04_admin_panel_biosignal_console_plan.md` | The admin dashboard and biosignal console design. |
| `06_audit_2026-04-28.md` | A codebase audit from April 2026. |
| `08_ui_localization.md` | How the browser UI became localizable. |
| `09_python_auto_update.md` | The Python-server update flow. |
| `10_biosignal_audit_and_limitations.md` | Sensor limitations and the research-grade boundary. |

## Still open from these plans

One item from `architecture-1.0-umbau.md` was planned and never built:

- **5f, the `mrg` CLI.** The plan assumed "the same local command API as the
  UI", which does not exist — the UI speaks HTTPS to Flask. Building it means
  either giving the CLI TLS and authentication or designing a new IPC surface,
  plus read-only offline inspection and exclusive maintenance locking that
  never bypasses a running runtime's ownership. `software/server.py` works
  regardless. See the 5f entry in that file for the full constraint list.

Item 4.10 in the same file is an unused placeholder, not open work.
