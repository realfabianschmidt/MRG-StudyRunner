# Biosignal Audit and Limitations

Status: Study Runner is a research-grade lab tool. It is not a medical device, not a diagnostic system, and not validated for GCP, 21 CFR Part 11, HIPAA, or clinical-trial source-data compliance.

## Current Sensor Setup

Study Runner can combine survey cards with these biosignal sources:

- BrainBit EEG via the BrainBit/NeuroSDK CLI. Runtime data can be streamed through LSL and summarized into sidecars and Notion summaries.
- MR60 mini radar via ESP32-C6 BLE firmware. Runtime data includes heart rate, breathing rate, distance, quality, packet sequence, drops, jitter, and timestamps.
- Tablet camera emotion via browser `getUserMedia` and a local DeepFace worker on the Study Runner server computer. Live-monitor frames can start as soon as the normal participant page is open and camera emotion is effectively enabled; study samples are recorded only after the participant ID is entered and the study has started.
- Study Runner LSL markers for study start/end, question shown/answered, and stimulus active start/stop.
- LabRecorder/XDF as the canonical raw-data recording path for synchronized LSL streams.

## Timing Model

The current design follows the professional pattern used in multimodal lab acquisition:

- Use LSL/XDF as the primary raw-data synchronization layer.
- Keep Study Runner event markers as an irregular LSL marker stream.
- Store `server_received_epoch_ms`, client trigger timestamps, card events, and interval boundaries in JSON sidecars/results.
- Summarize questions from `shown_at` to `answered_at`.
- Summarize stimuli from `active_started_at` to `active_ended_at`.
- Keep sensor device timestamps where available, but prefer LSL/server timing for cross-stream alignment unless a device provides a validated higher-precision clock.

This is aligned with the Lab Streaming Layer model for unified time-series collection, network sync, and XDF multi-stream recording:
https://labstreaminglayer.readthedocs.io/info/intro.html

The event model is conceptually close to BIDS events, where stimuli/responses are represented with onset and duration:
https://bids-specification.readthedocs.io/en/stable/modality-agnostic-files/events.html

The physiological sidecar approach is informed by BIDS physiological-recording metadata concepts such as sampling frequency, start time, columns, and device metadata:
https://bids-specification.readthedocs.io/en/stable/modality-specific-files/physiological-recordings.html

## What Is Strong Enough Now

- Modular integrations: sensors live behind integration plugins instead of one-off scripts.
- Explicit per-study sensor selection: BrainBit, MR60, and camera emotion are selected in study settings.
- Participant-ID gating: a study cannot start without a Participant ID as the first card.
- Pre-study camera monitoring: the normal participant page can stream a live camera/DeepFace monitor to the dashboard before recording starts.
- XDF/LSL core: raw streams and markers can be recorded in a standard multimodal lab format.
- Per-interval summaries: questions and stimuli have separate intervals for average biomarker summaries.
- Runtime control: study settings define the saved sensor defaults, while the dashboard can apply explicit temporary overrides for the current server session. Overrides are visible in the dashboard and can be reset to study settings.
- HTTPS for tablet camera: Study Runner generates a per-computer local Root CA and server certificate so iPad/tablet camera access can work over trusted HTTPS after the Root CA is installed on the tablet.
- Offline emotion model asset: the DeepFace emotion weights are vendored in the repo and bundled into packaged builds, reducing runtime dependence on GitHub model downloads.

## Known Weaknesses

- No regulatory validation package: there is no formal validation protocol, IQ/OQ/PQ evidence, locked SOP set, or release traceability matrix.
- No full audit trail: edits, operator actions, dashboard toggles, and data exports are not yet stored as immutable, time-stamped audit events.
- No electronic signatures or role-based approval workflow.
- No medical calibration guarantee: BrainBit, MR60, and DeepFace outputs are used as research signals, not clinical measurements.
- Camera emotion is model-dependent and sensitive to lighting, face angle, occlusion, demographic bias, and dependency versions.
- Dashboard overrides are operational controls, not a compliance audit trail. Operators must still document deviations from planned study settings when that matters for a study.
- The local HTTPS Root CA is generated per server computer. Tablets must trust the certificate for the actual server computer used in the session.
- BLE and browser timing are good enough for lab monitoring but should not be treated as hardware-grade trigger timing without external validation.
- LabRecorder must still be checked manually before a critical run to confirm that all intended streams are visible and recorded.

## Regulatory Boundary

21 CFR Part 11 focuses on trustworthy electronic records and signatures, including controls such as validation, access restrictions, record protection, and time-stamped audit trails:
https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11

Study Runner does not currently implement that full control set. It should be described as research-grade unless a separate validation and quality-management process is added.

## Recommended Lab Workflow

1. Open the active study in the admin hub.
2. Confirm the first card is Participant ID.
3. In study settings, select only the sensors needed for this study.
4. Open the dashboard and confirm BrainBit, MR60, LSL markers, LabRecorder/XDF, and camera worker status.
5. If a temporary dashboard override is needed, set it deliberately and leave the visible override warning in place until the run is finished, or reset to study settings before recording.
6. Open the tablet study page over trusted HTTPS. If camera emotion is effectively enabled, confirm the live monitor and detected face/emotion before starting.
7. Confirm LabRecorder sees the expected streams: StudyRunner markers, BrainBit streams, MR60 streams, and optional camera emotion streams.
8. Enter Participant ID on the tablet and start the study.
9. After completion, check the result JSON, sensor sidecars, XDF file, and Notion summary.

## Recommended Next Improvements

- Add an append-only operator audit log for config edits, dashboard toggles, study starts/stops, and exports.
- Add an XDF preflight check that verifies required LSL streams before a study can start.
- Export BIDS-like `events.tsv` and sidecar metadata for card events and stimulus intervals.
- Store exact integration versions, firmware version, BLE device IDs, BrainBit target identity, and DeepFace model/dependency versions per session.
- Add a lab preflight checklist UI with pass/fail status for each selected sensor.
- Add offline wheelhouse assets for Windows/macOS releases so Python package installation is reproducible without a live PyPI download.
