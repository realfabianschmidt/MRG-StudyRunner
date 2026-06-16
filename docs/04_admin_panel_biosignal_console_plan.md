# Admin Dashboard and Biosignal Console

This document describes the current Admin Dashboard behavior for biosignals and integration
control.

## Current model

Study Runner is a desktop-only server runtime for Windows, macOS, and Linux. The dashboard is the
operator-facing control room for optional integrations:

- BrainBit
- MR60/Mini-radar over ESP32C6 BLE
- tablet selfie-camera emotion capture
- local Emotion Worker
- LSL markers and sensor streams
- LabRecorder / XDF collection
- OSC / TouchDesigner
- Notion upload

The visible raw raw hardware panel has been removed. Common integration operation happens in
the dashboard. Notion setup happens in the full-page Notion Settings view.

## Dashboard entry behavior

The Live Dashboard button stays visible. The dashboard does not close automatically when no study
client heartbeat is present. Missing or stale study clients are shown as status information while
BrainBit, MR60, camera, and integration status continue to update.

## Integration controls

Dashboard controls are rendered from `/api/admin/status`. That payload is built from the internal
integration plugin registry. A control row can show:

- configured enabled/disabled state
- runtime status
- runtime actions when supported
- LSL state when relevant
- recording state when relevant
- scan timing fields for Bluetooth-like sensors
- latest activity and message fields

Generic routes handle runtime actions:

- `POST /api/admin/integrations/<key>/enabled`
- `POST /api/admin/integrations/<key>/start`
- `POST /api/admin/integrations/<key>/stop`
- `POST /api/admin/integrations/<key>/restart`

Older specific routes for BrainBit, MR60, and camera are kept as compatibility wrappers.

## Sensor scan policy

BrainBit and MR60 share the same status fields but intentionally use different retry policies.

- BrainBit: Start or Restart performs one 5-second scan attempt. If no device is found, the status
  explains the result and the operator can try again.
- MR60 BLE: while enabled, repeated 5-second BLE scan windows keep running. If `MR60_BLE` is not
  found, the adapter shows `waiting` and `next_retry_at`.

Both expose `scan_timeout_seconds`, `last_scan_started_at`, `last_scan_finished_at`, and
`next_retry_at` where applicable.

## Live cards

The dashboard includes focused cards for:

- connected study clients
- BrainBit quality, bands, mental values, calibration/status, and routing state
- MR60 heart rate, breath rate, presence, valid/stabilized flags, distance, phases, sequence/drop info, and jitter
- tablet camera emotion status, frame information, face detection, and worker state
- generated integration controls
- XDF and timestamp strategy

## Notion Settings

Notion is no longer configured in a small modal. The Admin Hub opens a full-page Notion Settings
view with dashboard-style cards:

- global Notion status and API-key storage state
- backend-local API key entry and clearing
- active-study Notion target settings
- queue status and queue flush action
- connection test
- setup guide

The real API key stays backend-local in `local_secrets.json` or an environment variable and is not
returned to the browser.

## Recording model

LSL/XDF remains the primary synchronization path. Completed participant output can also include
compact JSON sidecars when sensor history exists:

- `<participant>_mr60_signals.json`
- `<participant>_brainbit_signals.json`

Sidecars are exported through plugin callbacks. BrainBit, MR60, and camera emotion stay
sensor-near and separate; this dashboard does not create an early combined emotion score.

## Camera emotion defaults

Stimulus camera capture defaults target good tablet selfie-camera frames:

- 1280 x 720
- 200 ms interval, about 5 fps
- JPEG quality 0.85
- `facingMode: "user"`
- no audio
- raw-frame storage off by default

Browser camera access usually requires HTTPS on a real tablet.

## Related docs

- `docs/05_integration_plugin_guide.md` explains the registry lifecycle and how to add a sensor.
- `docs/01_project_overview_for_everyone.md` gives the high-level project overview.
- `README.md` contains the operator workflow and config examples.

