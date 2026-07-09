# DeepFace Model Assets

This folder is the preferred vendor location for DeepFace runtime model files
that should ship with Study Runner releases.

Expected asset for camera emotion analysis:

- `facial_expression_model_weights.h5`

The Local Emotion Worker repair action uses this folder first. If the file is
present and large enough, it is copied into the integration-local DeepFace cache,
normally:

```text
software/study_runner/integrations/local_emotion_worker/deepface_home/.deepface/weights/facial_expression_model_weights.h5
```

If the file is not present, the repair action falls back to the official
DeepFace model download.

Prepare the folder for offline/lab releases from the repository root:

```bash
python release_tools/fetch-deepface-model-assets.py
```
