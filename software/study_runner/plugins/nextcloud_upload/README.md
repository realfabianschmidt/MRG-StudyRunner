# Nextcloud upload plugin

Mirrors a study's finalized, immutable session artifacts to a Nextcloud
public share link, as an optional per-study upload destination. It never
runs during acquisition — it only publishes after a session has finalized.

## Architecture

Study Runner plugins are API v4: the core process supervises this plugin as
a subprocess and never imports its Python modules directly (see
`docs/file-guide.md`). This folder follows that same shape:

- `driver.py` — the only executable entry point. A one-line wrapper that
  calls `run_plugin_driver("nextcloud")`; the core never imports anything
  else in this folder.
- `plugin.py` — the actual plugin logic, running inside the supervised
  subprocess: `_status` (dashboard state), `_publish` (uploads one session's
  files after finalization), `_run_admin_action` (the `test_connection`
  admin action), `_validate_setting` (share-link format check).
- `webdav_client.py` — the WebDAV client that does the actual HTTP work
  against Nextcloud's public-share API.
- `manifest.json` — declares this as a `storage` / `upload_destination`
  capability plugin, its one credential (`STUDY_RUNNER_NEXTCLOUD_PASSWORD`),
  its one admin action (`test_connection`), and that it does not stream any
  live samples (`sample_delivery: none`) — it only moves already-finalized
  files.

## Where the code comes from

`webdav_client.py` is a **hand-written client, not a third-party library**.
It builds directly on `requests.Session()` and issues raw WebDAV verbs
(`PROPFIND`, `MKCOL`, `PUT`, `GET`) against Nextcloud's specific public-share
endpoints:

- `{base_url}/public.php/dav/files/<token>/...` — the current Nextcloud DAV
  endpoint, tried first.
- `{base_url}/public.php/webdav` — the legacy endpoint, used as a fallback
  for older Nextcloud servers.

This is Nextcloud-specific knowledge (not a generic RFC 4918 WebDAV client,
and not built on an existing WebDAV package such as `webdavclient3` or
`easywebdav`), reverse-engineered from Nextcloud's own public-share
behavior. Every upload is checksum-first: files are hashed and compared
before/after transfer so a corrupted or partial upload is detected rather
than silently accepted.

The only third-party dependency involved is `requests` itself (a normal
project dependency, see `software/requirements.txt`); there is no bundled
or copied Nextcloud SDK — Nextcloud does not publish one for this use case.

## Settings

- `share_link` (study setting) — the Nextcloud public share URL files get
  uploaded into.
- `password` (per-study credential, env var `STUDY_RUNNER_NEXTCLOUD_PASSWORD`
  or the local secret store) — the share's password, if the link requires
  one. Never written into an exported study file.

The admin action **Test connection** (in the machine settings hub) exercises
the same `webdav_client` path a real publish would use, so a bad share link
or password is caught before a study ever tries to upload.
