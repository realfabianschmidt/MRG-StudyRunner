const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 720;
const DEFAULT_INTERVAL_MS = 1000;
const MIN_INTERVAL_MS = 1000;
const FRAME_UPLOAD_TIMEOUT_MS = 2500;
const JPEG_QUALITY = 0.85;

function createSourceInstanceId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `camera-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function startCameraCaptureSession(options) {
  const intervalMs = Math.max(MIN_INTERVAL_MS, Number(options.intervalMs || DEFAULT_INTERVAL_MS));
  const getPayload = typeof options.getPayload === 'function' ? options.getPayload : () => ({});
  const onState = typeof options.onState === 'function' ? options.onState : () => {};
  const ingestPayload = options.ingestPayload;
  const preview = Boolean(options.preview);
  const activePhase = options.activePhase === undefined ? !preview : Boolean(options.activePhase);
  const getFrameState = typeof options.getFrameState === 'function'
    ? options.getFrameState
    : () => ({ preview, activePhase });
  const previewContainer = options.previewContainer || null;
  const sourceInstanceId = createSourceInstanceId();

  if (typeof ingestPayload !== 'function') {
    onState({ permission: 'failed', message: 'Camera upload service is unavailable.' });
    return () => {};
  }
  if (!window.isSecureContext) {
    onState({
      permission: 'insecure_context',
      message: 'Camera access needs trusted HTTPS on tablets. Install and fully trust the Study Runner local Root CA on the iPad, then open the https:// URL.',
    });
    return () => {};
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    onState({ permission: 'unsupported', message: 'Camera API is not available.' });
    return () => {};
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: 'user',
        width: { ideal: DEFAULT_WIDTH },
        height: { ideal: DEFAULT_HEIGHT },
      },
      audio: false,
    });
  } catch (error) {
    onState({ permission: 'denied', message: error.message || 'Camera permission denied.' });
    return () => {};
  }

  const video = document.createElement('video');
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  if (previewContainer) {
    video.className = 'camera-live-video';
    previewContainer.replaceChildren(video);
  }

  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { willReadFrequently: false });
  let sequenceNumber = 0;
  let framesSent = 0;
  let framesDropped = 0;
  let uploadInFlight = false;
  let activeUploadController = null;
  let stopped = false;
  let timerId = null;

  onState({ permission: 'granted', message: 'Camera capture started.' });
  await video.play().catch((error) => {
    onState({ permission: 'failed', message: error.message || 'Camera video could not start.' });
  });

  const captureFrame = () => {
    if (stopped || !context || video.readyState < 2) return;
    if (uploadInFlight) {
      framesDropped += 1;
      onState({
        permission: 'uploading',
        message: 'Camera upload busy; frame dropped.',
        frames_sent: framesSent,
        frames_dropped: framesDropped,
      });
      return;
    }

    const sourceWidth = video.videoWidth || DEFAULT_WIDTH;
    const sourceHeight = video.videoHeight || DEFAULT_HEIGHT;
    const scale = Math.min(DEFAULT_WIDTH / sourceWidth, DEFAULT_HEIGHT / sourceHeight, 1);
    const targetWidth = Math.max(1, Math.round(sourceWidth * scale));
    const targetHeight = Math.max(1, Math.round(sourceHeight * scale));
    canvas.width = targetWidth;
    canvas.height = targetHeight;
    context.drawImage(video, 0, 0, targetWidth, targetHeight);

    const sourceMonotonicMs = performance.now();
    const clientCapturedAt = new Date().toISOString();
    const image = canvas.toDataURL('image/jpeg', JPEG_QUALITY);
    const frameState = getFrameState() || {};
    const framePreview = frameState.preview === undefined ? preview : Boolean(frameState.preview);
    const frameActivePhase = frameState.activePhase === undefined ? activePhase : Boolean(frameState.activePhase);
    const frameSequenceNumber = sequenceNumber;
    sequenceNumber += 1;
    uploadInFlight = true;
    activeUploadController = new AbortController();

    void ingestPayload({
      ...getPayload({ sourceMonotonicMs, clientCapturedAt }),
      preview: framePreview,
      image,
      image_format: 'image/jpeg',
      width: targetWidth,
      height: targetHeight,
      client_captured_at: clientCapturedAt,
      source_monotonic_ms: sourceMonotonicMs,
      source_instance_id: sourceInstanceId,
      sequence_number: frameSequenceNumber,
      active_phase: frameActivePhase,
    }, {
      signal: activeUploadController.signal,
      timeoutMs: FRAME_UPLOAD_TIMEOUT_MS,
    })
      .then((response) => {
        if (stopped) return;
        const ingestResult = response?.result || response;
        if (ingestResult?.accepted) {
          framesSent += 1;
          onState({
            permission: 'uploading',
            message: `Camera frame ${frameSequenceNumber + 1} uploaded.`,
            frames_sent: framesSent,
            frames_dropped: framesDropped,
            analysis: ingestResult.analysis || null,
            frame: ingestResult.frame || null,
          });
          return;
        }
        onState({
          permission: 'upload_rejected',
          message: ingestResult?.reason
            ? `Camera frame rejected by backend: ${ingestResult.reason}`
            : 'Camera frame rejected by backend.',
          frames_sent: framesSent,
          frames_dropped: framesDropped,
        });
      })
      .catch((error) => {
        if (stopped && error?.name === 'AbortError') return;
        onState({
          permission: 'upload_failed',
          message: error.message || 'Camera frame upload failed.',
          frames_sent: framesSent,
          frames_dropped: framesDropped,
        });
      })
      .finally(() => {
        uploadInFlight = false;
        activeUploadController = null;
      });
  };

  captureFrame();
  timerId = window.setInterval(captureFrame, intervalMs);

  return () => {
    if (stopped) return;
    stopped = true;
    if (timerId !== null) window.clearInterval(timerId);
    if (activeUploadController) {
      activeUploadController.abort();
      activeUploadController = null;
    }
    stream.getTracks().forEach((track) => track.stop());
    video.srcObject = null;
    if (previewContainer && video.parentElement === previewContainer) {
      previewContainer.replaceChildren();
    }
    onState({ permission: 'stopped', message: 'Camera capture stopped.' });
  };
}
