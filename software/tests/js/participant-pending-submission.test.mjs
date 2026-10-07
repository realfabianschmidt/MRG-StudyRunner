import assert from 'node:assert/strict';
import test from 'node:test';
import { createPendingSubmissionStore } from '../../study_runner/apps/ui/scripts/participant/participant-pending-submission.js';

function memoryStorage() {
  const values = new Map();
  return {
    get length() { return values.size; },
    key(index) { return [...values.keys()][index] ?? null; },
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
  };
}

test('failed submissions survive a reload and retry with the same payload', async () => {
  globalThis.window = { localStorage: memoryStorage(), sessionStorage: memoryStorage() };
  const first = createPendingSubmissionStore({}, 'pending');
  const payload = { submission_id: 'submission-1', session_id: 'session-1', answers: [1] };
  first.persist(payload);
  const reloaded = createPendingSubmissionStore({}, 'pending');
  assert.deepEqual(reloaded.load('session-1'), payload);

  await reloaded.retryAll(async () => { throw new Error('offline'); });
  assert.deepEqual(reloaded.load('session-1'), payload);

  const sent = [];
  await reloaded.retryAll(async (item) => { sent.push(item); });
  assert.deepEqual(sent, [payload]);
  assert.equal(window.localStorage.length, 0);
});

test('session storage fallback retries when local storage is unavailable', async () => {
  const sessionStorage = memoryStorage();
  globalThis.window = {
    localStorage: {
      get length() { throw new Error('unavailable'); },
      setItem() { throw new Error('unavailable'); },
      removeItem() { throw new Error('unavailable'); },
      getItem() { throw new Error('unavailable'); },
    },
    sessionStorage,
  };
  const payload = { submission_id: 'submission-2', session_id: 'session-2', answers: [2] };
  createPendingSubmissionStore({}, 'pending').persist(payload);
  const sent = [];
  await createPendingSubmissionStore({}, 'pending').retryAll(async (item) => sent.push(item));
  assert.deepEqual(sent, [payload]);
  assert.equal(sessionStorage.getItem('pending'), null);
});

test('retrying one stored submission preserves a different fallback submission', async () => {
  const localStorage = memoryStorage();
  const sessionStorage = memoryStorage();
  const first = { submission_id: 'submission-3', session_id: 'session-3' };
  const second = { submission_id: 'submission-4', session_id: 'session-4' };
  localStorage.setItem('pending:session-3', JSON.stringify(first));
  sessionStorage.setItem('pending', JSON.stringify(second));
  globalThis.window = { localStorage, sessionStorage };

  await createPendingSubmissionStore({}, 'pending').retryAll(async (payload) => {
    if (payload.submission_id === second.submission_id) throw new Error('offline');
  });

  assert.equal(localStorage.getItem('pending:session-3'), null);
  assert.deepEqual(JSON.parse(sessionStorage.getItem('pending')), second);
});
