# Plan for an admin biosignal console and camera-based affect analysis

This plan describes how the current Study Runner admin page could grow into a central
control room for biosignals, camera-based affect analysis, and hardware service control.

Important:

- This is a staged plan, not a commitment to build everything at once.
- The goal is still a readable and maintainable local lab tool.
- Privacy, consent, and safe defaults are part of the design, not optional extras.

Current implementation slice:

- study pages can send lightweight heartbeats to the backend
- the admin backend can expose a merged status payload
- the admin page has first Settings and Dashboard entry points
- the Settings modal can load and save `hardware_config.json` as a full JSON editor
- the dashboard can show first BrainBit, Mini-radar, camera emotion, and XDF status cards
- Mini-radar and camera emotion adapter shells exist and can prepare LSL stream output
- stimulus cards can already carry Mini-radar and camera snapshot recording flags
- the Mini-radar adapter understands the V2026 JSON-style radar payload shape
- the camera worker can use OpenCV Haar face detection when enabled
- trained camera emotion inference still comes later

## Why this plan exists

The current admin page is mainly a study editor and preview surface.

The next step would be larger:

- live visibility into BrainBit and related biosignals
- snapshot-based or stream-based iPad selfie camera analysis for emotion-related estimates
- visual monitoring of radar-derived pulse and breathing
- raw or sensor-near recording of biosignals for later analysis
- control over hardware adapters and helper services from one place

If we do this, the admin page stops being only a "study editor" and becomes a
"study control and monitoring console".

## Target picture

The future admin panel should support four jobs in one place:

1. Study editing
2. Live biosignal monitoring
3. Camera-based affect analysis monitoring
4. Hardware and service control

## Updated target setup based on `C:\CodingProjects\V2026`

The V2026 folder is a useful reference, but the Study Runner version should not copy the
old device chain directly.

V2026 assumes this shape:

```text
MR60BHA2 radar -> ESP32C6 -> Raspberry Pi 4 -> optional Arduino display
USB camera     -> Raspberry Pi 4
```

The Study Runner target should be simpler:

```text
iPad study page selfie camera -> browser snapshots -> Study Runner backend -> emotion worker
Mini-radar                    -> USB or serial input -> Study Runner backend -> radar adapter
BrainBit                      -> repo-local BrainBit CLI -> Study Runner backend -> LSL / TouchDesigner
Admin browser                 -> dashboard view -> live status and service control
```

What should be reused from V2026:

- the general emotion categories
- the radar fields such as `heartRate`, `breathRate`, `heartPhase`, `breathPhase`, `presence`,
  and `quality`
- validation boundaries for heart and breathing rates
- auto-reconnect and stale-data handling ideas

What should not be reused directly:

- the early fusion idea that turns heart rate plus facial expression into one combined emotion
- Raspberry Pi startup and systemd assumptions
- Arduino display forwarding
- terminal-first UI
- camera capture through a local USB webcam
- one monolithic script that owns all devices at once

The Study Runner version should split this into small adapters and admin widgets.

## Recommended scope boundary

To keep this project realistic, the recommended first target is:

- BrainBit live monitoring
- optional camera snapshots every 500 to 1000 ms for emotion analysis
- overlay preview with face box and detected expression labels
- a compact radar or status widget for BrainBit and mini-radar measures
- service buttons for start, stop, restart, reconnect, and priming

The following should be treated as later or optional:

- full video streaming to Python
- continuous raw video recording by default
- automatic medical-grade pulse, respiration, muscle tone, or oxygen claims
- multimodal emotion fusion from facial expression, BrainBit, and mini-radar values
- multi-user concurrent admin control

## Important terminology

- `Affect analysis`: Estimation of visible facial expression or valence/arousal style features.
- `Snapshot mode`: The browser captures still frames at a fixed interval and sends them to the backend.
- `Live biosignal console`: The admin panel area that shows current values, status, and quality.
- `Priming`: A controlled warm-up step before a study run, such as opening a camera session,
  testing BrainBit contact, or verifying LSL streams.
- `Connected study client`: An iPad, tablet, phone, or browser tab that has the study page open
  and is sending a small heartbeat to the backend.
- `Rescue control`: A live-dashboard control that allows the study lead to recover from a
  problem during a run, such as restarting a worker or pausing a capture stream.
- `Raw-data-first`: The first goal is to record source data clearly. Interpretation and fusion
  should happen later during analysis unless there is a strong reason to do it live.
- `XDF`: The LabRecorder output format for synchronized Lab Streaming Layer recordings. It is
  easy to confuse with DXF, but the biosignal recording format here is `.xdf`.

## Reality check on the measurements

Some desired values have very different levels of technical confidence:

- `BrainBit EEG`: Directly supported by the current hardware path.
- `BrainBit-derived metrics`: Possible if the external processing chain is valid.
- `Pulse from mini-radar`: Possible if the hardware already provides this signal or a clean
  enough waveform for backend derivation.
- `Breathing from mini-radar`: Often a more realistic source than a selfie camera if the
  participant position and radar placement are stable.
- `Muscle tone`: Not realistic from a normal selfie camera alone. This would need a clearer
  hardware definition, for example EMG.
- `Blood oxygen`: Not realistic from a normal iPad selfie camera in a trustworthy way.

Recommendation:

- Do not label camera-derived values as clinical measurements.
- Present them as experimental estimates unless validated externally.
- Treat mini-radar pulse and breathing as sensor-derived biosignals, but still show signal
  quality and freshness so the study lead can judge whether values are usable.
- Do not infer one combined participant emotion from heart rate, BrainBit, and camera output in
  the first Study Runner implementation.

## Main architecture decision

The cleanest path for this repo is:

- keep the main Flask app
- add small backend services for biosignal state and camera snapshot ingestion
- add a dedicated admin dashboard area
- use lightweight polling or server push for status updates
- avoid a full WebRTC media server in the first version
- record raw or sensor-near data first, then analyze or fuse later

For the iPad camera, 500 ms snapshots mean roughly 2 frames per second. If the real target is
0.5 frames per second, configure the interval as 2000 ms instead. The implementation should make
this a setting rather than hard-coding one rate.

The more powerful Study Runner computer makes higher-quality emotion inference possible than the
old Raspberry Pi setup, because the backend can use heavier Python models without blocking the iPad.

Browser note:

- iPad camera access usually requires HTTPS.
- The local server can support a development HTTPS mode for camera testing.
- A real lab setup should use a trusted local certificate or a documented self-signed workflow.

## Proposed admin console layout

The admin page should be split into three main areas:

### 1. Study setup column

Keep the current responsibilities:

- study ID
- question list
- card editing
- save and load

Add two global actions:

- a `Settings` button that opens a modal for hardware and biosignal configuration
- a `Dashboard` button that appears only when at least one study client is connected

The normal admin page should stay useful even when no iPad is connected. The dashboard button
should become visible when the backend has received a recent heartbeat from a study page.

### 2. Live monitoring panel

Add a new panel for:

- BrainBit connection state
- battery and electrode quality
- latest EEG and derived values
- LSL state
- TouchDesigner routing state
- camera analysis state
- service health badges

This panel belongs to the dashboard view, not the normal study editing view.

### 3. Media and diagnostics panel

Add a panel for:

- latest iPad selfie snapshot
- face or landmark overlay
- detected expression labels
- confidence values
- pulse and breathing widgets from the mini-radar
- event log for warnings and reconnects

This panel should support "rescue" actions during a live run, but those actions must be explicit
and visible. The dashboard is not the primary place to configure the study before a run; it is the
place to monitor and fix problems during a run.

## Admin navigation model

The admin page should have three modes:

### Edit mode

Purpose:

- build and edit the study
- preview cards
- save the study configuration

Default:

- this is the page shown when `/admin` opens

### Settings modal

Purpose:

- configure hardware before the session starts
- edit BrainBit, mini-radar, camera, LSL, TouchDesigner, and LabRecorder settings
- save values into `hardware_config.json` or a future UI-managed hardware config layer

The modal should be available before any iPad is connected.

Suggested settings sections:

- `BrainBit`
- `Mini-radar`
- `iPad camera emotion analysis`
- `LSL and LabRecorder`
- `TouchDesigner`
- `Storage and privacy`
- `Service behavior`

### Live dashboard mode

Purpose:

- monitor a connected study client
- view BrainBit, mini-radar, camera emotion, and routing status
- use limited rescue controls during a study

Entry rule:

- show the dashboard button when a study page client is connected
- hide or disable it when no study client heartbeat has been seen recently

Exit rule:

- the user can always return to edit mode
- live services should continue running unless explicitly stopped

## Connected client detection

The study page should send a small heartbeat after it loads.

Suggested API:

```text
POST /api/study-client/heartbeat
GET /api/admin/status
```

The heartbeat payload should include:

- client ID
- participant ID if already entered
- current study state
- current question index
- whether a stimulus card is active
- camera permission state if known
- timestamp
- browser user agent summary

The admin status response should include:

- connected clients
- last heartbeat age
- active participant ID if known
- whether the dashboard button should be shown

Suggested stale timeout:

- mark the client stale after 5 seconds without heartbeat
- hide or disable dashboard after 15 seconds without heartbeat

## Recommended widgets

### BrainBit widget

Show:

- connected, stale, disconnected, failed
- battery
- quality per channel
- last EEG sample summary
- last bands summary
- last mental summary
- last update age

### Camera widget

Show:

- camera enabled or disabled
- permission state
- latest snapshot timestamp
- face detected yes or no
- overlay preview
- current expression labels and confidence

### Radar or compact biosignal widget

A radar chart can work for presentation, but it should not be the only display.

Use two forms together:

- one visual radar for quick comparison
- one plain numeric list for exact values

Suggested radar axes:

- BrainBit signal quality
- BrainBit selected band value or freshness
- contact quality
- radar pulse value or confidence
- radar breathing value or confidence
- camera emotion confidence

Avoid mixing units on the same chart without labels.

Do not use this radar chart as a fused emotion result. It is a live quality and status summary.

The mini-radar widget should also show exact values:

- heart rate in BPM
- breathing rate in breaths per minute
- presence detected yes or no
- distance if available
- signal quality
- heart phase if available
- breath phase if available
- last update age

### Service control widget

Show buttons for:

- start BrainBit service
- stop BrainBit service
- restart BrainBit service
- reconnect BrainBit
- prime camera analysis
- start or stop snapshot capture
- restart analysis worker
- prime mini-radar
- reconnect mini-radar
- refresh hardware state

Each action should return:

- success or failure
- timestamp
- short human-readable message

Rescue controls in the live dashboard should be smaller than the settings controls. They should
fix the current run, not silently rewrite the whole configuration.

## Backend additions

## 1. Biosignal status service

Add a backend service that provides a single merged admin status payload.

Suggested responsibility:

- collect state from BrainBit adapter
- collect state from the mini-radar adapter
- collect state from LSL adapter
- collect state from TouchDesigner routing
- collect state from camera analysis worker
- expose one normalized admin payload

Suggested file:

```text
app/admin_status_service.py
```

Suggested API:

```text
GET /api/admin/status
```

## 2. Hardware control routes

Add routes for safe service control.

Suggested API:

```text
POST /api/admin/brainbit/start
POST /api/admin/brainbit/stop
POST /api/admin/brainbit/restart
POST /api/admin/brainbit/reconnect
POST /api/admin/radar/start
POST /api/admin/radar/stop
POST /api/admin/radar/restart
POST /api/admin/camera/prime
POST /api/admin/camera/start
POST /api/admin/camera/stop
POST /api/admin/analysis/restart
POST /api/admin/hardware/refresh
```

These routes should stay thin and call small service functions.

## 3. Connected client service

Add a small service for tracking active study pages.

Suggested responsibility:

- receive heartbeats from `/`
- keep a short in-memory client registry
- mark clients as active, stale, or gone
- expose this to the admin dashboard through `/api/admin/status`

Suggested file:

```text
app/study_client_service.py
```

This does not need a database. A simple in-memory registry is enough for the local lab setup.

## 4. Camera snapshot ingestion

For a first version, avoid full video streaming.

Instead:

- the iPad study page captures a snapshot every 0.5 to 1.0 seconds
- snapshots are only sent when the study and the relevant stimulus card allow it
- the backend receives image frames and metadata
- a Python worker analyzes the frame for facial affect or expression
- the result is stored as a latest state plus optional per-participant time series

Suggested API:

```text
POST /api/camera/frame
```

Frame payload should include:

- participant ID
- study ID
- question index
- active phase flag
- timestamp
- image format
- image bytes or base64
- requested overlay mode
- snapshot sequence number

Processing recommendation:

- resize frames in the browser before upload
- default width around 320 to 640 pixels
- JPEG quality around 0.6 to 0.8
- store raw frames only when explicitly enabled

## 5. Affect analysis worker

Do not put heavy image processing directly into the request handler.

Use a separate worker responsibility:

- receive frames through a queue or small buffer
- run face detection
- run affect inference
- publish latest result state

Suggested file:

```text
app/integrations/camera_affect_adapter.py
```

V2026 has useful model/backend ideas, but the Study Runner worker should receive browser snapshots
instead of opening a local webcam with OpenCV.

Suggested worker modes:

- `mock`: no model, returns neutral/unknown for UI testing
- `opencv_haar`: face box and basic fallback only
- `opencv_cnn`: stronger expression model on the Study Runner computer
- `external_model`: later slot for a better model if chosen

## 6. Mini-radar adapter

The mini-radar should have its own adapter instead of being embedded in the camera or emotion code.

Suggested file:

```text
app/integrations/mini_radar_adapter.py
```

Suggested responsibility:

- open the configured serial or USB input
- parse JSON or line-based radar payloads
- validate heart and breathing values
- smooth values using configurable filters
- publish latest state for the admin dashboard
- optionally mirror radar values to LSL
- write per-participant radar output when enabled

Expected input fields, based on V2026:

- `heartRate`
- `breathRate`
- `present`
- `quality`
- `distance`
- `heartPhase`
- `breathPhase`
- `timestamp`

If the radar can connect directly to the Study Runner computer, prefer that. If a tiny bridge is
still required for electrical reasons, treat it only as a serial sensor interface, not as a
separate application layer and not as an Arduino display chain.

## 7. Fusion layer

V2026's `fusion_engine.py` should not be part of the first implementation path.

The original V2026 idea was to combine heart rate, breathing, and facial expression into one
inferred emotion. For the current Study Runner context, that is too interpretive too early.

Recommended current approach:

- record source streams separately
- show each source clearly in the admin dashboard
- use camera output only for the camera-derived emotion estimate
- keep BrainBit and mini-radar as raw or sensor-near biosignal data
- publish BrainBit, mini-radar, camera emotion, and Study Runner markers as LSL streams where practical
- record those streams together into one `.xdf` file with LabRecorder
- leave fusion for offline analysis or a later clearly validated feature

If fusion is added later, it should be:

- optional
- documented as experimental
- configurable
- saved separately from raw source data
- never used to overwrite the original measurements

Possible future file:

```text
app/offline_fusion_service.py
```

## 8. Result storage

Current participant output is already folder-based.

The preferred recording target for synchronized biosignals is `.xdf`, written by LabRecorder.
JSON files are still useful as readable sidecars, but the time-aligned raw or sensor-near streams
should ideally be available in the participant's `.xdf` file.

Extend it carefully with:

- `camera_analysis.json`
- optional overlay preview snapshots
- optional service log
- raw or sensor-near mini-radar samples
- raw or sensor-near BrainBit-derived samples when not already captured in `.xdf`

Suggested participant folder output:

```text
data/<study_id>/<participant_id>/
  <participant_id>.json
  <participant_id>.xdf
  <participant_id>_camera_analysis.json
  <participant_id>_radar_signals.json
  <participant_id>_brainbit_signals.json
  previews/
```

Default privacy recommendation:

- store derived features by default
- do not store raw face images unless explicitly enabled
- store camera emotion output as derived data
- store radar and BrainBit as raw or sensor-near biosignal data

Recommended LSL streams for `.xdf`:

- `StudyRunner` for stimulus start and stop markers
- `BrainBit_EEG`
- `BrainBit_BANDS`
- `BrainBit_MENTAL`
- `BrainBit_QUALITY`
- `BrainBit_BATTERY`
- `MiniRadar_VITALS`
- `MiniRadar_PHASES`
- `CameraEmotion`
- `CameraFaceQuality`

The participant folder should still copy or move the final `.xdf` into:

```text
data/<study_id>/<participant_id>/<participant_id>.xdf
```

## Timestamp strategy

LSL should be the primary synchronization layer for values that need to line up in time.

Recommended rule:

- use LSL timestamps as the primary timeline in `.xdf`
- keep source timestamps as metadata when they exist
- keep backend receive timestamps for debugging latency
- keep analysis timestamps for derived values such as camera emotion

Recommended timestamp fields:

```text
lsl_timestamp          Primary synchronization timestamp in the `.xdf`
source_timestamp       Sensor timestamp if the device provides one
client_captured_at     Browser-side capture time for iPad camera snapshots
server_received_at     Backend receive time
processed_at           Time when the Python worker produced the derived result
sequence_number        Per-source counter for loss and ordering checks
```

Practical handling per source:

- Study Runner markers: push directly to LSL at stimulus start and stop.
- BrainBit: use the current LSL push time unless the BrainBit SDK provides a reliable sample time.
- Mini-radar: use the current LSL push time plus the radar's own timestamp as metadata if available.
- Camera emotion: use the backend LSL push time for synchronization and store the browser capture
  time as metadata.

This is practical for the study context because the important alignment is between stimulus
phases, BrainBit, mini-radar, and camera-derived emotion values. It is not intended to make the
iPad camera behave like a frame-accurate clinical camera system.

## Frontend additions

## 1. Admin page structure

The current admin page would need a stronger layout system.

Recommended change:

- keep study editing in the left column
- add a dashboard mode with a right-side grid
- allow compact cards for hardware modules
- allow a larger media preview tile for the camera overlay
- add a top-level `Settings` button
- show a top-level `Dashboard` button only when a study client is connected

## 2. Admin controller

The admin controller should gain a dedicated dashboard layer.

Suggested responsibilities:

- poll or subscribe to `/api/admin/status`
- render hardware widgets
- call service control routes
- show status transitions and failures
- update snapshot preview and charts
- react to connected client state and show or hide the dashboard button
- open and save settings from the settings modal

The dashboard logic should be split out instead of growing one giant controller file.

Suggested files:

```text
static/js/admin-dashboard-controller.js
static/js/admin-settings-modal.js
static/js/admin-widgets/
  brainbit-widget.js
  camera-widget.js
  radar-widget.js
  service-widget.js
  connected-client-widget.js
```

## 3. Study page camera capture

The study page should gain optional camera capture logic.

Suggested responsibilities:

- request camera permission only when needed
- show a clear consent and recording state
- capture frames only during allowed phases
- stop capture immediately when the phase ends or the participant leaves
- send client heartbeats to the backend while the study page is open
- report camera permission state to the admin status service

Suggested file:

```text
static/js/camera-capture.js
static/js/study-client-heartbeat.js
```

## Configuration changes

The admin panel should eventually configure more than study cards.

Recommended new config layers:

### Global hardware config

Already partly exists in `hardware_config.json`.

Extend with:

- camera enabled
- snapshot interval
- overlay enabled
- raw image storage enabled
- affect worker enabled
- radar enabled
- radar pulse enabled
- radar breathing enabled
- radar LSL enabled
- camera emotion LSL enabled
- admin auto-refresh interval
- connected client stale timeout
- live dashboard enabled

### Per-stimulus settings

Add optional card fields such as:

- `camera_capture_enabled`
- `camera_snapshot_interval_ms`
- `camera_affect_analysis_enabled`
- `camera_overlay_preview_enabled`
- `camera_store_raw_frames`
- `mini_radar_recording_enabled`
- `brainbit_recording_enabled`

This follows the same idea already used for:

- `send_signal`
- `brainbit_to_lsl`
- `brainbit_to_touchdesigner`

## Privacy, consent, and ethics requirements

This part is mandatory.

Before implementation, define:

- whether raw face images are stored
- where they are stored
- how long they are kept
- who may access them
- how participants consent
- how to disable camera analysis for a participant immediately

Minimum safe defaults:

- camera analysis off by default
- explicit participant consent required
- no raw image storage by default
- derived features only by default
- one visible indicator when camera capture is active

## Service restart and priming strategy

The admin page can control services, but the rules must stay predictable.

Recommended rules:

- every control action is explicit
- restart actions are logged
- the UI always shows whether a service is running, starting, stale, or failed
- priming never silently starts recording without clear UI feedback
- settings changes should normally happen in the Settings modal before the run
- live dashboard changes should be treated as temporary rescue actions unless explicitly saved

Suggested priming flow before a study:

1. Open admin page
2. Prime BrainBit
3. Check channel quality
4. Prime mini-radar
5. Verify pulse and breathing signal quality
6. Prime camera
7. Verify face detection and overlay preview
8. Verify LSL visibility if used
9. Verify TouchDesigner connection if used
10. Start participant session

LabRecorder check:

- before a real run, verify that LabRecorder sees the Study Runner marker stream, BrainBit streams,
  mini-radar streams, and camera emotion streams
- after the run, Study Runner should copy or move the `.xdf` into the participant folder

## Live rescue behavior

The live dashboard should allow small emergency actions without forcing the researcher back into
the full settings modal.

Allowed rescue actions:

- restart BrainBit adapter
- reconnect mini-radar
- restart camera affect worker
- pause camera snapshots
- resume camera snapshots
- lower snapshot rate if the network is overloaded
- disable raw preview storage if disk or privacy concerns appear
- refresh LSL or TouchDesigner routing state

Not recommended as live rescue actions:

- changing participant ID
- changing study structure
- changing consent behavior
- changing default privacy storage policy

If a live rescue setting should become permanent, the UI should offer an explicit "Save to settings"
action rather than doing it silently.

## Delivery plan

## Phase 1: Admin status foundation

Goal:

Create a read-only biosignal dashboard before adding more control buttons.

Build:

- merged admin status API
- connected client heartbeat tracking
- conditional dashboard button in the admin page
- BrainBit widget
- mini-radar widget
- LSL and routing status
- event log

Complexity:

- medium

## Phase 2: Camera snapshot prototype

Goal:

Add iPad selfie camera snapshots with backend emotion analysis.

Build:

- camera permission flow
- snapshot capture every 500 to 1000 ms by default
- backend frame route
- worker-based face and affect analysis
- latest snapshot overlay preview in admin

Complexity:

- medium to high

## Phase 3: Derived biosignal dashboard

Goal:

Add compact visual summaries for pulse, breathing, camera emotion, BrainBit state, and signal quality.

Build:

- radar plus numeric widgets
- confidence display
- per-signal freshness
- signal quality badges
- raw-data recording indicators

Complexity:

- medium

## Phase 4: Service control and priming

Goal:

Make the admin page the central operational console.

Build:

- start, stop, restart, reconnect actions
- camera priming
- mini-radar priming
- analysis worker restart
- Settings modal
- live rescue controls
- audit log for actions

Complexity:

- medium

## Phase 5: Result integration and study-level controls

Goal:

Store per-participant analysis output and connect it to stimulus logic.

Build:

- participant-level camera analysis output
- per-stimulus camera toggles
- optional synchronized event markers
- LSL stream output for mini-radar and camera emotion values
- participant `.xdf` collection
- clear export structure

Complexity:

- medium

## Recommended implementation order

Build in this order:

1. merged admin status API
2. study client heartbeat and conditional dashboard button
3. Settings modal shell
4. BrainBit and mini-radar widgets
5. mini-radar adapter
6. camera snapshot prototype
7. emotion analysis worker
8. radar and derived widgets
9. service restart and rescue controls
10. per-stimulus camera and biosignal settings
11. participant-level storage and export

## Main risks

- Safari camera behavior on iPad can be finicky.
- Snapshot upload frequency can create load if images are too large.
- Emotion labels are scientifically weaker than they look in a UI.
- Mini-radar pulse and breathing quality depend strongly on placement and noise.
- Early multimodal fusion can look scientifically stronger than it really is.
- A dashboard button based on heartbeats must handle stale tabs gracefully.
- Live rescue controls can create confusion if they silently change persistent settings.
- Too many controls in one admin view can make the interface harder, not easier.

## Main design rule

The admin panel should become more powerful without becoming confusing.

That means:

- one place for monitoring
- one place for service control
- one modal for pre-run configuration
- simple wording
- clear signal freshness and confidence
- safe privacy defaults
- progressive rollout instead of one giant rewrite

## Definition of done for the first meaningful milestone

The first real milestone is done when:

- the admin page can show live BrainBit status cleanly
- the admin page shows a dashboard button when a study client is connected
- the admin page has a Settings modal for pre-run hardware configuration
- the admin page can show the latest camera snapshot with overlay
- the admin page can show mini-radar pulse and breathing status
- the backend can analyze snapshots without blocking requests
- service restart and priming actions work reliably
- participant output can include camera-derived features
- participant output can include mini-radar-derived biosignal features
- raw or sensor-near biosignal outputs are saved without forcing an early fusion result
- BrainBit, mini-radar, camera emotion, and Study Runner markers can be captured together in `.xdf`
- docs and consent text match the real behavior
