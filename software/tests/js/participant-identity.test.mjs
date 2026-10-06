import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.window = {
  sessionStorage: { getItem() { throw new Error('storage blocked'); } },
  crypto: { randomUUID: () => 'fixed-random-id' },
};
const { getStudyClientId, shouldYieldIdentity } = await import('../../study_runner/apps/ui/scripts/participant/study-client-heartbeat.js');
const { isOlderRunState } = await import('../../study_runner/apps/ui/scripts/participant/participant-session-recovery.js');
const { acknowledgedRunId } = await import('../../study_runner/apps/ui/scripts/participant/participant-result-submission.js');

test('identity remains stable when session storage is unavailable', () => {
  assert.equal(getStudyClientId(), 'study-client-fixed-random-id');
  assert.equal(getStudyClientId(), 'study-client-fixed-random-id');
});

test('newer cloned tabs yield their copied identity to the older tab', () => {
  assert.equal(shouldYieldIdentity(
    { startedAt: 200, pageToken: 'b' }, { startedAt: 100, pageToken: 'a' },
  ), true);
  assert.equal(shouldYieldIdentity(
    { startedAt: 100, pageToken: 'a' }, { startedAt: 200, pageToken: 'b' },
  ), false);
  assert.equal(shouldYieldIdentity(
    { startedAt: 100, pageToken: 'b' }, { startedAt: 100, pageToken: 'a' },
  ), true);
});

test('a delayed poll cannot reverse a newer run state', () => {
  assert.equal(isOlderRunState({ sequence: 7, status: 'loaded' }, { sequence: 8, status: 'running' }), true);
  assert.equal(isOlderRunState({ sequence: 8, status: 'running' }, { sequence: 7, status: 'loaded' }), false);
});

test('the device acknowledges only after it leaves the waiting screen', () => {
  const state = { studyRunState: { status: 'running', run_id: 'run-1' }, waitingForAdminStart: true };
  assert.equal(acknowledgedRunId(state), '');
  state.waitingForAdminStart = false;
  assert.equal(acknowledgedRunId(state), '');
  state.coverVisibleRunId = 'run-1';
  assert.equal(acknowledgedRunId(state), 'run-1');
  state.freshPageRequested = true;
  assert.equal(acknowledgedRunId(state), '');
});
