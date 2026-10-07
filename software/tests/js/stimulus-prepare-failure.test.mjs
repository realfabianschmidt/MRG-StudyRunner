// A stimulus whose server preparation fails: the dialog's three choices must
// each do exactly what they say, a stray tap/Escape must not stop the study,
// and a failed attempt is cancelled before any retry (never resent as-is).
import assert from 'node:assert/strict';
import test from 'node:test';

import { createParticipantStimulusExecution } from '../../study_runner/apps/ui/scripts/participant/participant-stimulus-execution.js';

globalThis.window ??= {};

class FakeElement {
  constructor() {
    this.bySelector = new Map();
  }

  querySelector(selector) {
    if (!this.bySelector.has(selector)) {
      const listeners = [];
      this.bySelector.set(selector, {
        listeners,
        addEventListener: (_type, handler) => listeners.push(handler),
        remove: () => {},
        click: () => listeners.forEach((handler) => handler()),
      });
    }
    return this.bySelector.get(selector);
  }

  set innerHTML(_value) {}
}

function harness({ prepareOutcomes, cancelOutcomes = [] }) {
  const sent = [];
  const modals = [];
  const calls = { next: 0, navigation: 0, abort: 0 };
  let eventCounter = 0;
  let prepareCall = 0;
  let cancelCall = 0;
  const state = { activeStimulus: null, questionMetrics: {}, clockRttMs: 10, config: {} };

  const question = { type: 'stimulus', duration_ms: 200000, warmup_duration_ms: 0, actuator_plugins: ['lamp'] };

  const execution = createParticipantStimulusExecution({
    state,
    getElement: () => null,
    createEventId: (prefix) => `${prefix}#${++eventCounter}`,
    shouldActivateHardware: () => true,
    postJson: async (path, payload) => {
      sent.push({ path, payload });
      if (path === '/api/trial/prepare') {
        const outcome = prepareOutcomes[Math.min(prepareCall, prepareOutcomes.length - 1)];
        prepareCall += 1;
        if (outcome === 'fail') throw new Error('invalid_trial_timing');
        return {};
      }
      if (path === '/api/trial/prepare/cancel') {
        const outcome = cancelOutcomes[Math.min(cancelCall, cancelOutcomes.length - 1)] ?? 'ok';
        cancelCall += 1;
        if (outcome === 'fail') throw new Error('network down');
        return { duplicate: false };
      }
      if (path === '/api/admin/study-run/stop') {
        calls.abort += 1;
        return {};
      }
      return {};
    },
    buildEventPayload: (index, q, phase, clientTriggerMs) => ({
      question_index: index, phase, client_trigger_ms: clientTriggerMs, actuator_plugins: q.actuator_plugins,
    }),
    estimateServerEpochMs: (value = 0) => 1_000_000 + value,
    updateNavigation: () => { calls.navigation += 1; },
    handleNext: async () => { calls.next += 1; },
    stopStudySensorSession: async () => {},
    resetParticipantSessionState: () => {},
    showWaitingForAdminStart: () => {},
    t: (_key, fallback) => fallback,
    showStudyNotice: () => {},
    reportNoticeToAdmin: () => {},
    createModal: (options) => {
      const modal = { element: new FakeElement(), body: new FakeElement(), options, openCount: 0, destroyed: false };
      modal.open = () => { modal.openCount += 1; };
      modal.close = () => {};
      modal.isOpen = () => !modal.destroyed;
      modal.setTitle = () => {};
      modal.destroy = () => { modal.destroyed = true; };
      modals.push(modal);
      return modal;
    },
    escapeHtml: (value) => String(value),
    startDeadlineTimer: () => ({ cancel() {}, tick() {} }),
    remainingWholeSeconds: () => 0,
    participantExtensions: { startStimulus: () => null },
    getParticipantSessionContext: () => ({}),
    sendReliableStudyEvent: async (path, payload) => {
      sent.push({ path, payload });
      return { server_received_epoch_ms: 1, marker_value: payload.marker_event };
    },
    closeVisibilityInterruption: () => {},
    getCardModule: () => ({ onStimulusPrepared: () => {}, onTimeUp: () => {} }),
    syncClock: async () => {},
    sendPartialResults: () => {},
    constants: { trialPrepareTimeoutMs: 1, trialStopTimeoutMs: 1, trialStartTimeoutMs: 1 },
  });

  const choose = (modal, choice) => modal.body.querySelector(`[data-prepare-${choice}]`).click();

  return { state, sent, modals, calls, execution, question, choose };
}

test('skip: the failed attempt is cancelled with tablet_skip and the next card follows', async () => {
  const h = harness({ prepareOutcomes: ['fail'] });
  const run = h.execution.startStimulusCard(4, h.question);
  await new Promise((resolve) => setTimeout(resolve, 0));
  h.choose(h.modals[0], 'skip');
  await run;

  const cancel = h.sent.find(({ path }) => path === '/api/trial/prepare/cancel').payload;
  assert.equal(cancel.reason, 'tablet_skip');
  assert.equal(h.calls.next, 1);
  assert.equal(h.state.activeStimulus, null);
  assert.equal(h.state.questionMetrics[4].prepare_resolution, 'skip');
});

test('abort: the attempt is cancelled with tablet_abort and the study is stopped', async () => {
  const h = harness({ prepareOutcomes: ['fail'] });
  const run = h.execution.startStimulusCard(4, h.question);
  await new Promise((resolve) => setTimeout(resolve, 0));
  h.choose(h.modals[0], 'abort');
  await run;

  const cancel = h.sent.find(({ path }) => path === '/api/trial/prepare/cancel').payload;
  assert.equal(cancel.reason, 'tablet_abort');
  assert.equal(h.calls.abort, 1);
});

test('a failed cancel reopens the dialog instead of resending the same attempt', async () => {
  const h = harness({ prepareOutcomes: ['fail'], cancelOutcomes: ['fail', 'ok'] });
  const run = h.execution.startStimulusCard(4, h.question);
  await new Promise((resolve) => setTimeout(resolve, 0));
  h.choose(h.modals[0], 'skip');
  await new Promise((resolve) => setTimeout(resolve, 0));

  // The cancel failed: a second dialog opens, and the original prepare must
  // not have been sent again.
  assert.equal(h.modals.length, 2);
  assert.equal(h.sent.filter(({ path }) => path === '/api/trial/prepare').length, 1);

  h.choose(h.modals[1], 'skip');
  await run;
  assert.equal(h.calls.next, 1);
  assert.equal(h.sent.filter(({ path }) => path === '/api/trial/prepare/cancel').length, 2);
});

test('retry: the old attempt is cancelled first, then a fresh attempt is sent with new ids', async () => {
  const h = harness({ prepareOutcomes: ['fail', 'ok'] });
  const run = h.execution.startStimulusCard(4, h.question);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const firstStimulusId = h.sent.find(({ path }) => path === '/api/trial/prepare').payload.stimulus_id;
  h.choose(h.modals[0], 'retry');
  await run;

  const cancel = h.sent.find(({ path }) => path === '/api/trial/prepare/cancel').payload;
  assert.equal(cancel.reason, 'tablet_retry');
  assert.equal(cancel.stimulus_id, firstStimulusId);
  const prepares = h.sent.filter(({ path }) => path === '/api/trial/prepare');
  assert.equal(prepares.length, 2);
  assert.notEqual(prepares[1].payload.stimulus_id, firstStimulusId);
  assert.equal(h.state.questionMetrics[4].prepare_failed, false);
});

test('closing the dialog (backdrop/Escape) reopens it instead of stopping the study', async () => {
  const h = harness({ prepareOutcomes: ['fail'] });
  const run = h.execution.startStimulusCard(4, h.question);
  await new Promise((resolve) => setTimeout(resolve, 0));

  const modal = h.modals[0];
  assert.equal(modal.element.querySelector('.overlay-close').listeners.length, 0, 'the close button was removed');
  modal.options.onClose();
  assert.equal(modal.openCount, 2, 'the dialog reopened (initial open + the reopen)');
  assert.equal(h.calls.abort, 0);
  assert.equal(h.sent.some(({ path }) => path === '/api/trial/prepare/cancel'), false);

  h.choose(modal, 'skip');
  await run;
  assert.equal(h.calls.next, 1);
});
