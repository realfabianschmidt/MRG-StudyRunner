import assert from 'node:assert/strict';
import { createTabletClock, CLOCK_SYNC_MAX_AGE_MS } from '../../study_runner/apps/ui/scripts/participant/tablet-clock.js';

let nowMs = 10_000;
let round = 0;
const delays = [40, 10, 25];
const clock = createTabletClock({
  now: () => nowMs,
  pause: async () => {},
  exchange: async (clientSendMs) => {
    const delay = delays[round++];
    const receive = clientSendMs + delay / 2 + 1_000_000;
    const send = receive + 2;
    nowMs += delay + 2;
    return { server_receive_ms: receive, server_send_ms: send };
  },
});

assert.equal(clock.evidence().time_source, 'server_receipt');
const selected = await clock.sync();
assert.equal(round, 3);
assert.equal(selected.network_delay_ms, 10);
assert.equal(selected.offset_ms, 1_000_000);
assert.equal(clock.evidence().clock_sync_rtt_ms, 10);
assert.equal(clock.evidence().source_epoch_ms, nowMs + 1_000_000);
assert.ok(clock.evidence().clock_sync_id);
nowMs += CLOCK_SYNC_MAX_AGE_MS + 1;
assert.deepEqual(clock.evidence(), {
  source_epoch_ms: null,
  clock_sync_id: null,
  clock_sync_age_ms: null,
  clock_sync_rtt_ms: null,
  time_source: 'server_receipt',
});

let calls = 0;
const broken = createTabletClock({
  now: () => 100,
  pause: async () => {},
  exchange: async () => { calls += 1; throw new Error('offline'); },
});
const [left, right] = await Promise.all([broken.sync(), broken.sync()]);
assert.equal(calls, 3);
assert.equal(left, null);
assert.equal(right, null);
assert.equal(broken.evidence().time_source, 'server_receipt');

const malformed = createTabletClock({
  now: () => 100,
  pause: async () => {},
  exchange: async () => ({ server_receive_ms: null, server_send_ms: null }),
});
assert.equal(await malformed.sync(), null);
assert.equal(malformed.evidence().time_source, 'server_receipt');

console.log('Tablet clock tests passed.');
