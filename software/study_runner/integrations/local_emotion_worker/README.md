# Local Emotion Worker

This folder contains the optional local emotion-analysis worker.

`server.py` in this folder is not the Study Runner main server. The main local app entrypoint is `software/server.py`.

Study Runner can manage this worker automatically when
`camera_emotion.enabled=true`, `camera_emotion.worker_mode=local_worker`, and
`camera_emotion.emotion_worker.auto_start=true`.

The worker can also be started manually from the dashboard for diagnostics,
even before a study is running. That only tests the model service; study data is
recorded later, after the tablet has a valid Participant ID and the study starts.

Manual run for debugging:

```bash
cd software/study_runner/integrations/local_emotion_worker
python server.py --port 3001
```

Then point the camera-emotion integration settings at the worker URL, normally
`http://127.0.0.1:3001`.

## Dashboard Workflow

1. Enable Camera Emotion in the study settings when the study needs it.
2. Open the participant tablet over HTTPS so `getUserMedia` is allowed.
3. The tablet sends preview frames while the Participant ID card is visible.
4. Enter the Participant ID and start the study; only then are active samples
   written as study data.
5. Watch the Camera Emotion card for Worker, Emotion, Confidence, Face, Frame,
   Processed and Message.

## Debugging

- `GET http://127.0.0.1:3001/status` should return `ready=true`.
- `model_ready=false` or `model_error` means DeepFace/OpenCV runtime setup is
  incomplete or failing on the server computer.
- If TensorFlow reports that `tf-keras` is required, use the dashboard button
  "Repair DeepFace runtime". This runs the same install as the command below,
  checks the emotion model weights and restarts the worker after a successful
  repair.
- If DeepFace reports a failed download for
  `facial_expression_model_weights.h5`, use "Repair DeepFace runtime" again.
  The repair first copies a vendored model asset from `model_assets/` when it is
  present, then falls back to the official DeepFace download. Manual fallback:
  place the file at `~/.deepface/weights/facial_expression_model_weights.h5`.
- Runtime log:
  `software/study_runner/integrations/local_emotion_worker/logs/emotion_worker_runtime.log`
- Install the standard Study Runner dependencies from the app root first, then
  restart the worker:

```bash
cd software
pip install -r requirements.txt
```

The local `requirements.txt` in this folder remains as a focused reference for
the worker, but the normal lab setup should use the app-level requirements file.

## Offline Wheelhouse

For release packaging, build platform-specific wheels from the repository root
on the target platform:

```bash
python release_tools/build-offline-wheelhouse.py
```

Install from a prepared wheelhouse without PyPI:

```bash
python -m pip install --no-index --find-links release_tools/wheelhouse/<platform> -r software/requirements.txt
```

## Vendored Model Weights

For robust lab releases, prepare the DeepFace emotion model asset before
building the release:

```bash
python release_tools/fetch-deepface-model-assets.py
```

That creates `software/study_runner/integrations/local_emotion_worker/model_assets/facial_expression_model_weights.h5`.
PyInstaller includes this folder in the server build, and the dashboard repair
action copies the model into the DeepFace cache without needing GitHub access on
the lab computer.
