import assert from 'node:assert/strict';
import test from 'node:test';

import { createParticipantExtension } from '../../study_runner/integrations/camera_emotion/ui/participant.js';

test('camera participant extension preserves preview, stimulus capture, cleanup, and retry', async () => {
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  const originalNavigator = globalThis.navigator;
  const intervals = [];
  const tracks = [];
  const frames = [];
  const participantActions = [];
  const statuses = [];
  let activeStimulus = null;
  let monitorStartFailures = 1;

  globalThis.window = {
    isSecureContext: true,
    setInterval(callback, intervalMs) {
      intervals.push({ callback, intervalMs });
      return intervals.length;
    },
    clearInterval() {},
  };
  globalThis.document = {
    createElement(tagName) {
      if (tagName === 'video') {
        return {
          muted: false,
          playsInline: false,
          srcObject: null,
          readyState: 2,
          videoWidth: 640,
          videoHeight: 480,
          play: async () => {},
        };
      }
      return {
        width: 0,
        height: 0,
        getContext: () => ({ drawImage() {} }),
        toDataURL: () => 'data:image/jpeg;base64,fixture',
      };
    },
  };
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      mediaDevices: {
        async getUserMedia() {
          const track = { stopped: false, stop() { this.stopped = true; } };
          tracks.push(track);
          return { getTracks: () => [track] };
        },
      },
    },
  });

  const runParticipantAction = async (actionKey, payload) => {
    participantActions.push({ actionKey, payload });
    if (actionKey === 'start_monitor' && monitorStartFailures > 0) {
      monitorStartFailures -= 1;
      throw new Error('temporary monitor outage');
    }
    assert.ok(['start_monitor', 'stop_monitor'].includes(actionKey));
    return { ok: true };
  };
  const ingestParticipant = async (ingestKey, payload) => {
    assert.equal(ingestKey, 'frame');
    frames.push(payload);
    return { ok: true, result: { accepted: true } };
  };
  const context = {
    isEnabled: () => true,
    getConfig: () => ({ questions: [{ type: 'stimulus' }] }),
    getActiveStimulus: () => activeStimulus,
    getSession: () => ({ participantId: 'p01', studyId: 'study-a', sessionId: 'session-a' }),
    getPluginActions: () => ({ snapshot_interval_ms: 1400 }),
    runParticipantAction,
    ingestParticipant,
    estimateServerEpochMs: (value) => value + 100,
    getClientClockOffsetMs: () => 12,
    reportStatus: (status) => statuses.push(status),
  };

  try {
    const extension = createParticipantExtension(context);
    await extension.startPrestudyMonitor();
    assert.equal(tracks.length, 0);
    assert.equal(extension.getHeartbeatStatus().state, 'warning');
    extension.onRuntimeChange({ enabled: true, session: { questionsBuilt: true } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await Promise.resolve();
    assert.equal(intervals[0].intervalMs, 1400);
    assert.equal(frames[0].preview, true);
    assert.equal(frames[0].phase, 'prestudy_monitor');
    assert.ok(frames[0].source_instance_id);

    activeStimulus = { index: 2, activeStarted: true, question: { type: 'stimulus' } };
    intervals[0].callback();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(frames.at(-1).preview, false);
    assert.equal(frames.at(-1).active_phase, true);
    assert.equal(frames.at(-1).question_index, 2);
    assert.equal(frames.at(-1).source_instance_id, frames[0].source_instance_id);

    extension.beforeSubmit();
    assert.equal(tracks[0].stopped, true);
    assert.equal(extension.getHeartbeatStatus().monitor_active, false);

    const cleanup = await extension.startStimulus({ stimulus: activeStimulus });
    assert.equal(typeof cleanup, 'function');
    assert.equal(tracks.length, 2);
    cleanup();
    assert.equal(tracks[1].stopped, true);

    extension.onSubmitFailed();
    await extension.startPrestudyMonitor();
    assert.equal(tracks.length, 3);
    assert.equal(extension.getHeartbeatStatus().monitor_active, true);
    extension.dispose();
    assert.equal(tracks[2].stopped, true);
    assert.ok(participantActions.some(({ actionKey }) => actionKey === 'stop_monitor'));
    assert.ok(statuses.some((status) => status.state === 'active'));
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
    Object.defineProperty(globalThis, 'navigator', {
      configurable: true,
      value: originalNavigator,
    });
  }
});
