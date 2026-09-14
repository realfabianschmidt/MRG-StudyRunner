import { startCameraCaptureSession } from './camera-capture.js';

const MONITOR_START_TIMEOUT_MS = 3000;
const DEFAULT_INTERVAL_MS = 1000;
const MIN_INTERVAL_MS = 1000;

function normalizedInterval(value) {
  const intervalMs = Number(value);
  return Math.max(
    MIN_INTERVAL_MS,
    Number.isFinite(intervalMs) && intervalMs > 0 ? intervalMs : DEFAULT_INTERVAL_MS,
  );
}

export function createParticipantExtension(context) {
  let permission = 'not_requested';
  let lastError = '';
  let monitorActive = false;
  let captureActive = false;
  let framesSent = 0;
  let framesDropped = 0;
  let monitorCleanup = null;
  let monitorStarting = null;
  let monitorBackendActive = false;
  let monitorGeneration = 0;
  const stimulusCleanups = new Set();

  const report = (extra = {}) => {
    const state = lastError
      ? 'warning'
      : (captureActive || monitorActive ? 'active' : (context.isEnabled() ? 'ready' : 'disabled'));
    context.reportStatus({
      enabled: context.isEnabled(),
      state,
      permission,
      monitor_requested: context.isEnabled(),
      monitor_active: monitorActive,
      capture_active: captureActive,
      frames_sent: framesSent,
      frames_dropped: framesDropped,
      last_error: lastError,
      ...extra,
    });
  };

  const applyCaptureState = (captureState, source) => {
    permission = captureState?.permission || permission;
    framesSent = Number(captureState?.frames_sent ?? framesSent);
    framesDropped = Number(captureState?.frames_dropped ?? framesDropped);
    const running = ['granted', 'uploading'].includes(permission);
    if (source === 'monitor') monitorActive = running;
    if (source === 'stimulus') captureActive = running;
    lastError = running || permission === 'stopped'
      ? ''
      : (captureState?.message || lastError || 'Camera capture is unavailable.');
    report();
  };

  const activeFrameState = () => {
    const stimulus = context.getActiveStimulus();
    const active = Boolean(
      context.isEnabled()
      && stimulus?.activeStarted
      && stimulus?.question?.type === 'stimulus'
    );
    return { preview: !active, activePhase: active };
  };

  const captureInterval = (question = null) => {
    const configQuestions = context.getConfig()?.questions || [];
    const stimulus = question || configQuestions.find((candidate) => candidate?.type === 'stimulus');
    const actions = stimulus ? context.getPluginActions(stimulus) : {};
    return normalizedInterval(actions?.snapshot_interval_ms ?? stimulus?.camera_snapshot_interval_ms);
  };

  const framePayload = (frameTiming = {}) => {
    const session = context.getSession();
    const stimulus = context.getActiveStimulus();
    const active = !activeFrameState().preview;
    const question = active ? stimulus?.question : null;
    const sourceMonotonicMs = frameTiming.sourceMonotonicMs;
    return {
      participant_id: session.participantId,
      study_id: session.studyId,
      question_index: active ? stimulus?.index ?? null : null,
      question_type: active ? question?.type || '' : 'prestudy_monitor',
      phase: active ? 'stimulus_active' : 'prestudy_monitor',
      session_id: session.sessionId,
      client_clock_offset_ms: context.getClientClockOffsetMs(),
      source_monotonic_ms: sourceMonotonicMs ?? null,
      source_epoch_ms: Number.isFinite(sourceMonotonicMs)
        ? context.estimateServerEpochMs(sourceMonotonicMs)
        : null,
    };
  };

  const stopMonitor = () => {
    const notifyBackend = monitorBackendActive;
    monitorBackendActive = false;
    monitorGeneration += 1;
    monitorStarting = null;
    if (typeof monitorCleanup === 'function') {
      try {
        monitorCleanup();
      } finally {
        monitorCleanup = null;
      }
    }
    monitorActive = false;
    report();
    if (notifyBackend && typeof context.runParticipantAction === 'function') {
      void context.runParticipantAction('stop_monitor', {}, {
        timeoutMs: MONITOR_START_TIMEOUT_MS,
      }).catch((error) => {
        lastError = error?.message || 'Camera monitor cleanup could not be confirmed.';
        report();
      });
    }
  };

  const startMonitor = () => {
    if (!context.isEnabled() || monitorCleanup) return Promise.resolve();
    if (monitorStarting) return monitorStarting;
    const generation = ++monitorGeneration;
    lastError = '';
    report({ state: 'starting' });
    monitorStarting = (async () => {
      try {
        await context.runParticipantAction('start_monitor', {
          study_id: context.getSession().studyId,
        }, { timeoutMs: MONITOR_START_TIMEOUT_MS });
        monitorBackendActive = true;
        const cleanup = await startCameraCaptureSession({
          ingestPayload: (payload, options) => context.ingestParticipant('frame', payload, options),
          intervalMs: captureInterval(),
          preview: true,
          activePhase: false,
          getFrameState: activeFrameState,
          getPayload: framePayload,
          onState: (captureState) => applyCaptureState(captureState, 'monitor'),
        });
        if (generation !== monitorGeneration || !context.isEnabled()) {
          cleanup?.();
          return;
        }
        monitorCleanup = typeof cleanup === 'function' ? cleanup : null;
      } catch (error) {
        monitorActive = false;
        lastError = error?.message || 'Camera monitor could not start.';
        report();
      } finally {
        if (generation === monitorGeneration) monitorStarting = null;
      }
    })();
    return monitorStarting;
  };

  const startStimulusCapture = async ({ stimulus } = {}) => {
    if (!context.isEnabled() || stimulus?.question?.type !== 'stimulus') return null;
    if (monitorStarting) await monitorStarting;
    if (monitorCleanup) return null;

    const cleanup = await startCameraCaptureSession({
      ingestPayload: (payload, options) => context.ingestParticipant('frame', payload, options),
      intervalMs: captureInterval(stimulus.question),
      getPayload: (frameTiming) => {
        const session = context.getSession();
        return {
          participant_id: session.participantId,
          study_id: session.studyId,
          question_index: stimulus.index,
          question_type: stimulus.question.type,
          session_id: session.sessionId,
          client_clock_offset_ms: context.getClientClockOffsetMs(),
          source_monotonic_ms: frameTiming?.sourceMonotonicMs ?? null,
          source_epoch_ms: Number.isFinite(frameTiming?.sourceMonotonicMs)
            ? context.estimateServerEpochMs(frameTiming.sourceMonotonicMs)
            : null,
        };
      },
      onState: (captureState) => applyCaptureState(captureState, 'stimulus'),
    });
    let stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      cleanup?.();
      stimulusCleanups.delete(stop);
      captureActive = false;
      report();
    };
    stimulusCleanups.add(stop);
    return stop;
  };

  const stopStimulusCaptures = () => {
    for (const cleanup of [...stimulusCleanups]) cleanup();
  };

  report();
  return {
    onRuntimeChange({ enabled, session }) {
      if (!enabled) {
        stopMonitor();
        stopStimulusCaptures();
      } else if (session?.studyStarted || session?.questionsBuilt) {
        // Runtime polling doubles as a reconnect trigger after a transient
        // monitor-start failure. startMonitor() itself is idempotent.
        void startMonitor();
      }
      report();
    },
    startPrestudyMonitor() {
      return startMonitor();
    },
    stopPrestudyMonitor() {
      stopMonitor();
    },
    startStimulus(payload) {
      return startStimulusCapture(payload);
    },
    beforeSubmit() {
      stopMonitor();
      stopStimulusCaptures();
    },
    onSubmitFailed() {
      lastError = '';
      report();
    },
    getHeartbeatStatus() {
      return {
        enabled: context.isEnabled(),
        state: lastError ? 'warning' : (captureActive || monitorActive ? 'active' : 'ready'),
        permission,
        monitor_requested: context.isEnabled(),
        monitor_active: monitorActive,
        capture_active: captureActive,
        frames_sent: framesSent,
        frames_dropped: framesDropped,
        last_error: lastError,
      };
    },
    dispose() {
      stopMonitor();
      stopStimulusCaptures();
    },
  };
}
