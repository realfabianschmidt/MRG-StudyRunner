# Updates

Verifying and applying a Study Runner update: a release archive (the normal
path), a git clone, or a signed packaged build (dormant). This is the trust and
file-replacement half; the half that actually talks to the admin dashboard —
checking for a release, downloading it, deciding between the packaged and
source-mode paths — is `runtime_core/settings/update_service.py`, which
calls into this folder rather than duplicating it.

## Files

| File | What it does | In / Out |
|---|---|---|
| `signatures.py` | Defines the bytes covered by a release signature, platform names such as `windows-x86_64`, and Ed25519 verification against the trusted keys. Both `tools/study_runner_manager.py` and `release_tools/build_python_update_manifest.py` import this module. Its wire format is frozen because installed 0.3.x clients verify against these bytes. | a release manifest's asset entry → verified, or a `SignatureVerificationError` |
| `trusted_keys.py` | The list of Ed25519 public keys a release is allowed to be signed with — empty in a source checkout, replaced with real keys from CI secrets when a packaged build is produced. | — |
| `archive_update.py` | Release-archive updates, shared by the in-app and the terminal updater: SHA-256 check, safe extraction into `.tools/update-staging/`, program-file swap that never moves studies/results/settings (old files go to `.tools/update-backup/`), content merge, and a rollback that restores what it can. On Windows every staged path goes through `fs_path` (`\?\` form), so it works without the long-paths policy. | a downloaded archive → the new program files in place, or the old ones back |
| `installer.py` | The detached helper that runs after the server exited: for an archive install it swaps the files, runs the install script and starts the new version (or, on any failure, the old one again); for a git clone it restarts `server.py`; for a packaged build it launches the staged executable. It clears the staged update afterwards, so the panel never offers a stale "Restart now". | a staged update's state file → the new version running, or a recorded failure |
| `__init__.py` | One-paragraph map of the files and where the request-facing half lives instead. | — |
