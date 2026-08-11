# Camera emotion plugin

`camera_emotion` is the single public plugin for tablet camera acquisition,
the backend LSL bridge, and derived emotion values. Its local DeepFace process
is an implementation detail under `worker/`; it is not a second catalog
plugin.

The plugin supports two worker modes:

- `local_worker`: Study Runner supervises the bundled worker process.
- `remote_worker`: frames are sent to the configured worker URL.

`local_worker` is supported on Windows x64 and macOS Apple Silicon. On macOS
Intel with CPython 3.12, TensorFlow/tf-keras has no compatible wheel; use
`remote_worker`. Camera capture, the host LSL bridge, and XDF recording remain
supported on Intel.

Disabling the whole study plugin stops acquisition. Native LSL publication is
mandatory whenever the plugin is enabled and has no separate kill switch.

The manifest-declared `ui/participant.js` owns tablet preview/capture,
stimulus cleanup, submit retry, and participant heartbeat details. The generic
study controller only invokes the common participant-extension lifecycle and
does not contain a `camera_emotion` branch.

## Architecture (API v4)

Like every Study Runner plugin, the core process never imports this folder's
Python modules directly — it only ever starts `driver.py` as a subprocess
(see `docs/file-guide.md`). Inside that subprocess:

- `driver.py` — the only executable entry point (`run_plugin_driver("camera_emotion")`).
- `plugin.py` — the single public plugin: config, lifecycle, participant
  actions, and admin actions (`repair_runtime`, `install_dependencies`).
- `adapter.py` — accepts tablet camera frames and publishes the stable LSL
  streams.
- `worker/` — the internal DeepFace analysis process (`server.py`), its
  supervisor (`worker/plugin.py`), the per-frame analyzer
  (`worker/analyzer.py`), and shared error classification
  (`worker/model_errors.py`). This is an implementation detail the plugin
  supervises, not a second catalog plugin (see above).

## Where the code comes from

`worker/analyzer.py` calls `DeepFace.analyze(frame, actions=["emotion"],
enforce_detection=False, detector_backend="opencv", silent=True)` — standard,
documented usage of the official `deepface` PyPI package (pinned in
`software/requirements.txt`), not adapted or copied vendor example code.

The emotion model weight file itself is a separate, VGG-Face-derived
artifact under non-commercial research terms; `worker/model_errors.py` pins
its exact upstream URL and SHA-256, matching the authoritative attribution
in the repository's `THIRD_PARTY_NOTICES.md`. It must be provisioned
explicitly (see below) — it is never bundled or auto-downloaded silently.

## Local worker diagnostics

Source checkout on Windows x64 or macOS Apple Silicon:

```bash
cd software
python study_runner/plugins/camera_emotion/worker/server.py --port 3001
```

The normal platform install script installs the local analysis dependencies on
supported hosts. `python server.py --emotion-worker-self-test --json` provides
the source-runtime self-test. The installer and repair action use the shared
Python 3.12 constraints and install only `opencv-python`; the incompatible
parallel `opencv-python-headless` distribution is deliberately excluded because
both packages own the same `cv2` namespace.

`GET http://127.0.0.1:3001/status` reports model readiness. The generic plugin
admin actions `repair_runtime` and `install_dependencies` repair the Python
environment, but never download model weights. The emotion weight is separately
licensed and absent from source releases. Review the repository's
`THIRD_PARTY_NOTICES.md` and provision it explicitly with the documented CLI
acknowledgement, or use `remote_worker` with a suitably licensed model.

The canonical runtime cache lives below
`<StudyRunner data>/runtime/camera_emotion/worker/`. An existing v2 cache below
`runtime/local_emotion_worker/` is reused for one compatibility release.
