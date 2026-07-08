import { postJson } from './api-client.js';
import { startCameraCaptureSession } from './camera-capture.js';

let cleanup = null;

const messageEl = document.getElementById('camera-preview-message');
const resultEl = document.getElementById('camera-preview-result');
const previewEl = document.getElementById('camera-preview-video');
const startButton = document.getElementById('btn-camera-preview-start');
const stopButton = document.getElementById('btn-camera-preview-stop');

startButton?.addEventListener('click', () => {
  void startPreview();
});

stopButton?.addEventListener('click', () => {
  stopPreview();
});

async function startPreview() {
  if (cleanup) return;
  setMessage('Starting camera emotion preview ...');
  try {
    await postJson('/api/admin/camera/preview/start', {});
  } catch (error) {
    setMessage(error.message || 'Could not start camera preview runtime.');
    return;
  }

  cleanup = await startCameraCaptureSession({
    preview: true,
    activePhase: false,
    intervalMs: 500,
    previewContainer: previewEl,
    getPayload: () => ({
      study_id: 'camera-preview',
      participant_id: 'preview',
      question_index: null,
      question_type: 'preview',
    }),
    onState: renderPreviewState,
  });
  startButton.disabled = true;
  stopButton.disabled = false;
}

function stopPreview() {
  if (typeof cleanup === 'function') {
    cleanup();
  }
  cleanup = null;
  startButton.disabled = false;
  stopButton.disabled = true;
  void postJson('/api/admin/camera/preview/stop', {}).catch((error) => {
    console.warn('[camera-preview] Could not stop preview runtime:', error);
  });
}

function renderPreviewState(state) {
  setMessage(state.message || state.permission || 'Camera preview running.');
  const analysis = state.analysis || {};
  if (!analysis || !Object.keys(analysis).length) {
    return;
  }
  const emotion = analysis.emotion || 'unknown';
  const confidence = Number(analysis.confidence || 0);
  const face = analysis.face_detected ? 'face detected' : 'no face';
  const error = analysis.error ? ` - ${analysis.error}` : '';
  resultEl.textContent = `${emotion} (${confidence.toFixed(2)}) - ${face}${error}`;
}

function setMessage(message) {
  if (messageEl) {
    messageEl.textContent = message;
  }
}

window.addEventListener('beforeunload', () => {
  if (cleanup) {
    stopPreview();
  }
});
