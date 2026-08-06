import assert from 'node:assert/strict';

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

const localStorage = memoryStorage();
const sessionStorage = memoryStorage();
globalThis.window = {
  localStorage,
  sessionStorage,
  crypto: { randomUUID: () => 'fixture-id' },
  setTimeout,
  clearTimeout,
};

const calls = [];
let releaseStart;
globalThis.fetch = async (endpoint, options) => {
  const payload = JSON.parse(options.body);
  calls.push({ endpoint, payload });
  if (endpoint === '/api/start') {
    await new Promise((resolve) => { releaseStart = resolve; });
  }
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => ({ received_event_id: payload.event_id }),
  };
};

const {
  flushReliableStudyEvents,
  sendReliableStudyEvent,
} = await import('../../study_runner/frontend/scripts/shared/reliable-event-queue.js');

const start = sendReliableStudyEvent('/api/start', {
  event_id: 'event-start',
  source_monotonic_ms: 100,
});
await Promise.resolve();
await Promise.resolve();
assert.deepEqual(calls.map((call) => call.endpoint), ['/api/start']);

const stop = sendReliableStudyEvent('/api/stop', {
  event_id: 'event-stop',
  source_monotonic_ms: 250,
});
await Promise.resolve();
assert.deepEqual(calls.map((call) => call.endpoint), ['/api/start']);

releaseStart();
const [startResponse, stopResponse] = await Promise.all([start, stop]);
assert.equal(startResponse.received_event_id, 'event-start');
assert.equal(stopResponse.received_event_id, 'event-stop');
assert.deepEqual(calls.map((call) => call.endpoint), ['/api/start', '/api/stop']);
assert.equal(localStorage.getItem('study-runner-pending-events-v3'), null);

const duplicateResponse = await sendReliableStudyEvent('/api/start', {
  event_id: 'event-start',
  source_monotonic_ms: 100,
});
assert.equal(duplicateResponse.received_event_id, 'event-start');
assert.equal(calls.length, 2);
await assert.rejects(
  sendReliableStudyEvent('/api/start', {
    event_id: 'event-start',
    source_monotonic_ms: 101,
  }),
  /reused with different data/,
);

globalThis.fetch = async () => { throw new Error('offline'); };
await assert.rejects(
  sendReliableStudyEvent('/api/start', {
    event_id: 'event-offline',
    source_monotonic_ms: 777,
    source_epoch_ms: 1_700_000_000_777,
  }),
  /offline/,
);
const stored = JSON.parse(localStorage.getItem('study-runner-pending-events-v3'));
assert.equal(stored.length, 1);
assert.equal(stored[0].payload.source_monotonic_ms, 777);
assert.equal(stored[0].payload.source_epoch_ms, 1_700_000_000_777);

let retriedPayload = null;
globalThis.fetch = async (_endpoint, options) => {
  retriedPayload = JSON.parse(options.body);
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => ({ ok: true }),
  };
};
await flushReliableStudyEvents();
assert.equal(retriedPayload.event_id, 'event-offline');
assert.equal(retriedPayload.source_monotonic_ms, 777);
assert.equal(localStorage.getItem('study-runner-pending-events-v3'), null);
