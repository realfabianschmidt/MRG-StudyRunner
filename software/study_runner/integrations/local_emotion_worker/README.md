# Local Emotion Worker

This folder contains a separate optional emotion-analysis worker.

`server.py` in this folder is not the Study Runner main server. The main local app entrypoint is `software/server.py`.

The worker can be run separately when tablet camera emotion analysis should happen through a local HTTP service:

```bash
cd software/study_runner/integrations/local_emotion_worker
python server.py --port 3001
```

Then point the camera-emotion integration settings at the worker URL, for example `http://127.0.0.1:3001`.
