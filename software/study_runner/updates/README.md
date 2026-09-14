# Updates

Verifying and applying a signed Study Runner update. This is the trust and
file-replacement half; the half that actually talks to the admin dashboard —
checking for a release, downloading it, deciding between the packaged and
source-mode paths — is `runtime_core/settings/update_service.py`, which
calls into this folder rather than duplicating it.

## Files

| File | What it does | In / Out |
|---|---|---|
| `signatures.py` | Defines the bytes covered by a release signature, platform names such as `windows-x86_64`, and Ed25519 verification against the trusted keys. Both `tools/study_runner_manager.py` and `release_tools/build_python_update_manifest.py` import this module. Its wire format is frozen because installed 0.3.x clients verify against these bytes. | a release manifest's asset entry → verified, or a `SignatureVerificationError` |
| `trusted_keys.py` | The list of Ed25519 public keys a release is allowed to be signed with — empty in a source checkout, replaced with real keys from CI secrets when a packaged build is produced. | — |
| `installer.py` | The actual file swap, run as its own short-lived process so it can replace files the currently-running server can't touch itself: for a packaged build, launches the newly staged executable; for a source-mode update (where `update_service.py` already ran `git pull` and the install script in place), just restarts `server.py` from the same folder. | a staged update's state file → the new version running, or a recorded failure |
| `__init__.py` | One-paragraph map of the three files and where the request-facing half lives instead. | — |
