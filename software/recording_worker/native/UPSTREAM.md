# App-LabRecorder source lock

The native core keeps an unmodified copy of the small LabRecorder XDFWriter
surface that it uses. `UPSTREAM_LOCK.json` pins the public `v1.17.1` tag to
commit `8419550553e4336dd46378a9a871b3065a70b895` and records the SHA-256 of
each copied file.

Build manifests also store `source_lock_sha256`, the SHA-256 of the complete
lock object serialized as sorted, compact UTF-8 JSON. The locator checks this
fingerprint as well as the live core hash and ABI probe, binding every staged
library to the reviewed source set.

LabRecorder's checked-out files use CRLF line endings on Windows, while source
archives and non-Windows worktrees may expose LF. The setup verifier therefore
decodes each file as UTF-8, normalizes line endings to CRLF, and hashes those
bytes. No other whitespace or content normalization is permitted. This makes
the check reproducible on Windows and macOS without weakening it to a semantic
comparison.

The Study Runner adaptations are separate files in `native/src`. Do not edit
the files below `native/vendor/App-LabRecorder`. To update the upstream
version, review the upstream changes, replace the copied files, update every
lock field, and rerun the native fixtures and merge-parity tests.
