# Study Runner App

Dieser Ordner ist die eigentliche Anwendung:

- `app_server.py`: internes Flask-App-Modul fuer Browser- und Desktop-Modus. Lokal gestartet wird ueber `../server.py`.
- `backend/`: Flask-App, Routen und Services.
- `web/`: Admin- und Teilnehmeroberflaeche.
- `integrations/`: eingebaute Integrationen wie BrainBit, OSC, LSL, Notion und Kamera.

Normale Studieninhalte liegen nicht hier, sondern in `study_content/`.
