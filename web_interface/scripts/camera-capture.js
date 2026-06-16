import { postJson } from './api-client.js';

const DEFAULT_WIDTH = 1280;
const DEFAULT_HEIGHT = 720;
const DEFAULT_INTERVAL_MS = 200;
const JPEG_QUALITY = 0.85;

export async function startCameraCaptureSession(options) {
  const intervalMs = Math.max(DEFAULT_INTERVAL_MS, Number(options.intervalMs || DEFAULT_INTERVAL_MS));
  const getPayload = typeof options.getPayload === 'function' ? options.getPayload : () => ({});
  const onState = typeof options.onState === 'function' ? options.onState : () => {};

  if (!window.isSecureContext) {
    onState({
      permission: 'insecure_context',
      message: 'Camera access needs HTTPS on tablets. Start the server with STUDY_RUNNER_HTTPS=1 and open the https:// URL.',
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

  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { willReadFrequently: false });
  let sequenceNumber = 0;
  let stopped = false;
  let timerId = null;

  onState({ permission: 'granted', message: 'Camera capture started.' });

  await video.play().catch((error) => {
    onState({ permission: 'failed', message: error.message || 'Camera video could not start.' });
  });

  const captureFrame = () => {
    if (stopped || !context || video.readyState < 2) {
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

    const clientCapturedAt = new Date().toISOString();
    const image = canvas.toDataURL('image/jpeg', JPEG_QUALITY);

    void postJson('/api/camera/frame', {
      ...getPayload(),
      image,
      image_format: 'image/jpeg',
      width: targetWidth,
      height: targetHeight,
      client_captured_at: clientCapturedAt,
      sequence_number: sequenceNumber,
      active_phase: true,
    })
      .then((response) => {
        const nextFrameCount = sequenceNumber + 1;
        if (response?.accepted) {
          onState({
            permission: 'uploading',
            message: `Camera frame ${nextFrameCount} uploaded.`,
            frames_sent: nextFrameCount,
          });
          return;
        }

        onState({
          permission: 'upload_rejected',
          message: response?.reason
            ? `Camera frame rejected by backend: ${response.reason}`
            : 'Camera frame rejected by backend.',
          frames_sent: sequenceNumber,
        });
      })
      .catch((error) => {
        onState({
          permission: 'upload_failed',
          message: error.message || 'Camera frame upload failed.',
          frames_sent: sequenceNumber,
        });
      });

    sequenceNumber += 1;
  };

  captureFrame();
  timerId = window.setInterval(captureFrame, intervalMs);

  return () => {
    stopped = true;
    if (timerId !== null) {
      window.clearInterval(timerId);
    }
    stream.getTracks().forEach((track) => track.stop());
    video.srcObject = null;
    onState({ permission: 'stopped', message: 'Camera capture stopped.' });
  };
}