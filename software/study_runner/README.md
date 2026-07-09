# Study Runner App Code

This folder contains the runnable application code.

- `app_server.py`: internal Flask app module used by browser mode and packaged mode. It also prepares the per-computer HTTPS certificate used for tablet camera access.
- `backend/`: Flask routes and backend services.
- `web/`: admin page, participant page, scripts, styles, cards, fonts, and locales.
- `integrations/`: built-in integrations such as BrainBit, OSC, LSL, Notion, LabRecorder, MR60, the Local Emotion Worker, and tablet camera emotion.

For local browser-mode development, start the app through the single main entrypoint:

```bash
cd software
python server.py
```

Normal study content is stored in `software/study_content/`, not in this code package.
Integration runtime logs, generated DeepFace caches, and generated SSL certificates are local runtime state and should stay out of Git.
