import assert from 'node:assert/strict';
import test from 'node:test';

test('a cloned tab gets a different connection before its first heartbeat', async () => {
  const stored = new Map();
  let nextId = 0;
  globalThis.window = {
    crypto: { randomUUID: () => `id-${++nextId}` },
    sessionStorage: {
      getItem: (key) => stored.get(key) || null,
      setItem: (key, value) => stored.set(key, value),
    },
    setTimeout,
  };
  const channels = [];
  globalThis.BroadcastChannel = class {
    constructor(name) { this.name = name; channels.push(this); }
    postMessage(data) {
      channels.filter((peer) => peer !== this && peer.name === this.name)
        .forEach((peer) => queueMicrotask(() => peer.onmessage?.({ data })));
    }
  };
  const older = await import('../../study_runner/apps/ui/scripts/participant/study-client-heartbeat.js?older');
  const newer = await import('../../study_runner/apps/ui/scripts/participant/study-client-heartbeat.js?newer');
  const firstReady = older.prepareStudyClientIdentity();
  const copiedId = older.getStudyClientId();
  const secondReady = newer.prepareStudyClientIdentity();
  await Promise.all([firstReady, secondReady]);
  assert.equal(older.getStudyClientId(), copiedId);
  assert.notEqual(newer.getStudyClientId(), copiedId);
});
