// What happens when a stimulus card's time is up: continue automatically, or
// let the participant stay ("overtime") until Next. Sensors are never
// stopped here; the selected actuators are, exactly once, with either the
// time-up event or the moment the card is left.
import assert from 'node:assert/strict';
import test from 'node:test';

import { createParticipantStimulusExecution } from '../../study_runner/apps/ui/scripts/participant/participant-stimulus-execution.js';

globalThis.window ??= {};

function harness(question) {
  const sent = [];
  const timers = [];
  const hooks = [];
  const calls = { next: 0, navigation: 0 };
  let eventCounter = 0;
  const state = { activeStimulus: null, questionMetrics: {}, clockRttMs: 10, config: {} };
  const execution = createParticipantStimulusExecution({
    state,
    getElement: () => null,
    createEventId: (prefix) => `${prefix}#${++eventCounter}`,
    shouldActivateHardware: () => true,
    postJson: async (path, payload) => {
      sent.push({ path, payload });
      return {};
    },
    buildEventPayload: (index, q, phase, clientTriggerMs) => ({
      question_index: index,
      phase,
      client_trigger_ms: clientTriggerMs,
      actuator_plugins: q.actuator_plugins,
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
    createModal: () => ({}),
    escapeHtml: (value) => String(value),
    startDeadlineTimer: (options) => {
      const timer = { options, cancelled: false, cancel() { this.cancelled = true; }, tick() {} };
      timers.push(timer);
      return timer;
    },
    remainingWholeSeconds: () => 0,
    participantExtensions: { startStimulus: () => null },
    getParticipantSessionContext: () => ({}),
    sendReliableStudyEvent: async (path, payload) => {
      sent.push({ path, payload });
      return { server_received_epoch_ms: 1, marker_value: payload.marker_event };
    },
    closeVisibilityInterruption: () => {},
    getCardModule: () => ({
      onStimulusPrepared: (q, index) => hooks.push(['prepared', index]),
      onTimeUp: (q, index, context) => hooks.push(['timeUp', index, context.overtime]),
    }),
    constants: { trialPrepareTimeoutMs: 1, trialStopTimeoutMs: 1, trialStartTimeoutMs: 1 },
  });
  const run = async () => {
    await execution.startStimulusCard(4, question);
    await new Promise((resolve) => setTimeout(resolve, 0));
  };
  const fireLastTimer = async () => {
    timers.at(-1).options.onDeadline({ callbackDelayMs: 0 });
    await new Promise((resolve) => setTimeout(resolve, 0));
  };
  return { state, sent, timers, hooks, calls, execution, run, fireLastTimer };
}

const trialEvents = (sent) => sent
  .filter(({ path }) => path !== '/api/trial/prepare')
  .map(({ path, payload }) => `${path} ${payload.marker_event}`);

const baseQuestion = {
  type: 'stimulus', duration_ms: 30000, warmup_duration_ms: 0, actuator_plugins: ['lamp'],
};

test('auto-advance: the time is up, the actuators stop and the next card follows', async () => {
  const h = harness({ ...baseQuestion, auto_advance: true });
  await h.run();
  const prepare = h.sent.find(({ path }) => path === '/api/trial/prepare').payload;
  assert.equal(prepare.actuator_stop_at, 'stop');
  assert.equal(prepare.time_up_event_id, '');
  assert.equal(prepare.stop_deadline_epoch_ms, prepare.planned_deadline_epoch_ms);

  await h.fireLastTimer();
  assert.deepEqual(trialEvents(h.sent), ['/api/start stimulus_active_start', '/api/stop stimulus_active_stop']);
  assert.equal(h.calls.next, 1);
  assert.equal(h.state.questionMetrics[4].time_up_at, undefined, 'no overtime, no time-up');
  assert.deepEqual(h.hooks, [['prepared', 4], ['timeUp', 4, false]]);
});

test('overtime with actuators stopping at time-up: stop with the time-up event, a marker on leaving', async () => {
  const h = harness({ ...baseQuestion, auto_advance: false, overtime_keep_actuators: false, overtime_max_ms: 60000 });
  await h.run();
  const prepare = h.sent.find(({ path }) => path === '/api/trial/prepare').payload;
  assert.equal(prepare.actuator_stop_at, 'time_up');
  assert.match(prepare.time_up_event_id, /time-up/);
  assert.equal(prepare.stop_deadline_epoch_ms, prepare.planned_deadline_epoch_ms);

  await h.fireLastTimer();
  assert.equal(h.calls.next, 0, 'the card stays');
  assert.equal(h.state.activeStimulus.overtime, true, 'Next is free during overtime');
  const timeUp = h.sent.at(-1);
  assert.equal(timeUp.path, '/api/stop');
  assert.equal(timeUp.payload.marker_event, 'stimulus_time_up');
  assert.equal(timeUp.payload.event_id, prepare.time_up_event_id);
  assert.equal(h.state.questionMetrics[4].time_up_event_id, prepare.time_up_event_id);

  await h.execution.stopActiveStimulus({ shouldSendStop: true });
  assert.deepEqual(trialEvents(h.sent), [
    '/api/start stimulus_active_start',
    '/api/stop stimulus_time_up',
    '/api/marker stimulus_active_stop',
  ]);
  assert.ok(Number.isFinite(h.state.questionMetrics[4].overtime_ms));
  assert.equal(h.state.activeStimulus, null);
});

test('overtime with actuators running on: a marker at time-up, the stop on leaving', async () => {
  const h = harness({ ...baseQuestion, auto_advance: false, overtime_keep_actuators: true, overtime_max_ms: 60000 });
  await h.run();
  const prepare = h.sent.find(({ path }) => path === '/api/trial/prepare').payload;
  assert.equal(prepare.actuator_stop_at, 'stop');
  assert.equal(prepare.stop_deadline_epoch_ms, prepare.planned_deadline_epoch_ms + 60000);

  await h.fireLastTimer();
  await h.execution.stopActiveStimulus({ shouldSendStop: true });
  assert.deepEqual(trialEvents(h.sent), [
    '/api/start stimulus_active_start',
    '/api/marker stimulus_time_up',
    '/api/stop stimulus_active_stop',
  ]);
});

test('the maximum overtime continues the card by itself', async () => {
  const h = harness({ ...baseQuestion, auto_advance: false, overtime_max_ms: 10000 });
  await h.run();
  await h.fireLastTimer();
  const overtimeTimer = h.timers.at(-1);
  assert.equal(overtimeTimer.options.deadlineMs - overtimeTimer.options.startedAtMs, 10000);
  await h.fireLastTimer();
  assert.equal(h.state.questionMetrics[4].overtime_capped, true);
  assert.equal(h.calls.next, 1);
});

test('every trial event names the selected actuators', async () => {
  const h = harness({ ...baseQuestion, auto_advance: false });
  await h.run();
  await h.fireLastTimer();
  await h.execution.stopActiveStimulus({ shouldSendStop: true });
  for (const { path, payload } of h.sent.filter(({ path }) => path !== '/api/trial/prepare')) {
    assert.deepEqual(payload.actuator_plugins, ['lamp'], path);
  }
});
