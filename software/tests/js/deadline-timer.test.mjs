import assert from 'node:assert/strict';
import { startDeadlineTimer, remainingWholeSeconds } from '../../study_runner/web/scripts/lib/deadline-timer.js';

let nowMs = 1_000;
let scheduled = null;
let finished = null;
const ticks = [];

const timer = startDeadlineTimer({
  durationMs: 1_000,
  now: () => nowMs,
  schedule: (callback, delayMs) => {
    scheduled = { callback, delayMs };
    return 1;
  },
  cancelScheduled: () => {},
  onTick: (tick) => ticks.push(tick),
  onDeadline: (result) => { finished = result; },
});

assert.equal(timer.deadlineMs, 2_000);
assert.equal(ticks[0].remainingMs, 1_000);
assert.equal(remainingWholeSeconds(1_001), 2);
assert.ok(scheduled);

// Simulate a hidden/throttled tab: the callback does not run for 2.5 seconds.
// One observation must complete against the original deadline, not add ticks.
nowMs = 3_500;
scheduled.callback();
assert.equal(ticks.at(-1).remainingMs, 0);
assert.equal(ticks.at(-1).progress, 1);
assert.equal(finished.callbackDelayMs, 1_500);
assert.equal(timer.isFinished(), true);

// A visibilitychange handler may explicitly observe the timer before its next
// scheduled UI tick. That observation must replace, not fork, the callback.
let manualNowMs = 10_000;
let nextTimerId = 10;
const scheduledCallbacks = new Map();
const canceledTimerIds = [];
const manualTimer = startDeadlineTimer({
  durationMs: 1_000,
  now: () => manualNowMs,
  schedule: (callback) => {
    const timerId = nextTimerId;
    nextTimerId += 1;
    scheduledCallbacks.set(timerId, callback);
    return timerId;
  },
  cancelScheduled: (timerId) => {
    canceledTimerIds.push(timerId);
    scheduledCallbacks.delete(timerId);
  },
});
assert.deepEqual([...scheduledCallbacks.keys()], [10]);

manualNowMs = 10_250;
manualTimer.tick();
assert.deepEqual(canceledTimerIds, [10]);
assert.deepEqual([...scheduledCallbacks.keys()], [11]);

manualNowMs = 11_100;
scheduledCallbacks.get(11)();
assert.equal(manualTimer.isFinished(), true);
