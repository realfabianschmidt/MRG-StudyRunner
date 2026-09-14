# Shared

The small, dependency-light utilities more than one area of the app needs,
that belong to none of them specifically. The one rule that keeps this
folder useful: nothing here may import from the server, recording, plugin,
or frontend code — that's what lets, say, both the Flask host and a
plugin's own detached subprocess depend on the same helper without either
one having to import the other.

## Files

| File | What it does | In / Out |
|---|---|---|
| `atomic_io.py` | Crash-safe file writes: a JSON or byte payload is always either the previous complete file or the new complete one on disk, never a half-written one (temp file + flush + fsync + atomic rename). Also the one path-locking primitive that serializes a read-modify-write against the same file across concurrent requests. | a payload + a path → a safely written file |
| `runtime_mode.py` | Answers "is this a packaged build, and where do bundled files live?" — the one place that checks `sys.frozen` so nothing else has to. | — → `is_frozen()`, the project's base directory, the app mode |
| `software_root.py` | Finds the real `software/` folder by walking upward looking for `server.py` + `study_runner/`, instead of counting `..` levels — a fixed directory-depth guess silently starts pointing at the wrong folder the moment a module moves, and the operator-visible symptom is as vague as "where did my studies go". | a starting path → the confirmed `software/` root, or a clear error |
| `dependency_utils.py` | Best-effort install of an optional dependency (like `pylsl`) at startup, or a plain "install it yourself" message when auto-install is disabled — so a plugin whose optional SDK is missing degrades to "unavailable" instead of crashing the server. | a list of optional packages → installed, or a clear reason they aren't |
| `filename_sanitizer.py` | Turns an arbitrary participant/study/session identifier into a safe filename fragment. | any string → a filesystem-safe, length-bounded fragment |
| `study_identifiers.py` | The one place a study's stable id is normalized — used identically for its saved filename and its credential-storage key, so renaming a study can never strand its secrets under the old key. | a study's display id → its normalized, stable key |
| `system_clock_probe.py` | Checks whether the system clock looks plausible (not years off) and whether a time-sync service appears to be running — never a network time check, since labs record offline on purpose. Both signals are reported honestly as bounds, not proof the clock is actually correct. | — → a plausibility verdict + evidence, for the recording preflight |
| `__init__.py` | States the one rule this whole folder exists to enforce. | — |
