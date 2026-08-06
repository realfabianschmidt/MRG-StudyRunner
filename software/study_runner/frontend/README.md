# Frontend — everything the browser loads

No build step, no bundler, no CDN. These are plain ES modules and plain CSS,
served straight from disk. A lab machine is often offline, so the page must work
with nothing but this folder.

```text
frontend/
  pages/     the two HTML documents: study.html (participant) and admin.html
  scripts/   the ES modules those two pages load
  styles/    main.css and the design tokens everything else uses
  locales/   en.json and de.json, one flat key set each
  fonts/     Materiability, the heading face (first-party)
  vendor/    third-party assets: Geist and the Iconoir icon set
```

## scripts/

| Folder | What it owns |
|---|---|
| `participant/` | What a participant sees: the study run itself and the heartbeat that tells the admin someone is there. |
| `admin/` | The operator's side: the study editor, the live dashboard, the session browser and its timeline. |
| `cards/` | One module per card type. `index.js` is the registry; adding a card type means adding a file and a line there. |
| `settings/` | The settings shells. `machine/` is per-computer, `study/` is per-study. |
| `shared/` | Used by more than one of the above: the API client, translations, the plugin catalog, view models, small DOM helpers. |

## Rules that are tested, not just intended

- **The URL prefix is `/static/`, not the folder name.** `static_folder` is set
  once in `backend/__init__.py`; renaming this folder does not move a single
  `href`.
- **Nothing loads from a CDN.** `test_pages_do_not_load_from_cdns` fails the
  build if it does.
- **Both locales carry the same keys.** A key added to one and not the other is
  a test failure, not a missing translation at runtime.
- **No core script may name a plugin.** Plugin-specific interface comes from the
  plugin's own `ui/*.js`, declared in its manifest and served through
  `/api/plugins/<key>/assets/…`. `test_web_ui.py` guards this.
- **Pure logic goes in a view model.** `shared/timeline-view-model.js` and
  `shared/finalization-view-model.js` hold no DOM and are tested directly with
  `node --test`; the modules that draw them stay thin.
