# Software

Everything needed to run Study Runner from source. Start with
[`study_runner/README.md`](study_runner/README.md) for the application
itself — this page is only the map of what sits around it at this level.

```text
software/
  server.py          the one command to start Study Runner: `python server.py`
  requirements.txt    the app's Python dependencies (version ranges)
  constraints/        the exact, tested pins that resolve those ranges
  study_runner/       the application itself — see its own README
  recording_worker/   the native (C++) XDF core, a different language on purpose
  tests/              the whole test suite — see its own README
```

## `server.py`

The single entry point, and more than it looks: running it plainly
(`python server.py`) starts the web server, but it also re-invokes itself
with a flag to become one of several detached background processes —
`--recording-worker`, `--plugin-driver <key>`, `--emotion-worker`,
`--brainbit-cli`. That's why a packaged build (which has no separate Python
interpreter lying around) can still launch its own worker processes: it
re-runs its own executable with a different flag instead of shelling out to
a script that wouldn't exist in the bundle.

## `requirements.txt` and `constraints/`

Two different jobs, deliberately not merged into one file:

- **`requirements.txt`** says *what* the app needs, as version ranges
  (`flask>=3.0`) — the honest, human-readable dependency list.
- **`constraints/py312-bootstrap.txt`, `py312-common.txt`,
  `py312-local-emotion.txt`** say exactly *which* versions within those
  ranges are the ones actually tested and shipped, split by install phase
  (bootstrapping pip itself, the common runtime, and the optional local
  emotion-detection stack). The install flows (`tools/install-windows.cmd`,
  `tools/install-macos.sh`) install `requirements.txt` constrained by these
  files, so "it works on my machine" and "it works in the release" are
  pinned to the same versions.

## `recording_worker/`

Not Python — the vendored, MIT-licensed native XDF core (C++, built with
CMake) that actually writes the recording files. It has its own detailed
README. It lives outside `study_runner/` on purpose: a different language
and build toolchain doesn't belong inside a Python package, and
`data_core/worker/core.py` only ever talks to its compiled output through
`ctypes`, never its source directly.

## What's generated, not shipped

`.build/`, `.tmp/`, `.pytest_cache/`, `build/`, `dist/`, `logs/`, and
`runtime/` are all gitignored — build output, test caches, and local runtime
state, regenerated as needed and safe to delete if they ever grow stale or
just take up space. `study_content/` (the studies an operator actually
edits, plus this machine's settings) and `saved_results/` (recorded
sessions) are the operator's real data and are never touched by a build or
a cleanup.

## Running it

```bash
cd software
python server.py
```
