/**
 * Monotonic deadline timer.
 *
 * Callback frequency is deliberately irrelevant to elapsed time. Browsers may
 * throttle this callback while a tab is hidden; the next callback still observes
 * the real `performance.now()` deadline and completes immediately when overdue.
 */
export function startDeadlineTimer(options = {}) {
  const now = typeof options.now === 'function' ? options.now : () => performance.now();
  const schedule = typeof options.schedule === 'function'
    ? options.schedule
    : (callback, delayMs) => window.setTimeout(callback, delayMs);
  const cancelScheduled = typeof options.cancelScheduled === 'function'
    ? options.cancelScheduled
    : (timerId) => window.clearTimeout(timerId);
  const onTick = typeof options.onTick === 'function' ? options.onTick : () => {};
  const onDeadline = typeof options.onDeadline === 'function' ? options.onDeadline : () => {};
  const tickIntervalMs = Math.max(16, Number(options.tickIntervalMs || 100));
  const startedAtMs = Number.isFinite(Number(options.startedAtMs))
    ? Number(options.startedAtMs)
    : now();
  const deadlineMs = Number.isFinite(Number(options.deadlineMs))
    ? Number(options.deadlineMs)
    : startedAtMs + Math.max(0, Number(options.durationMs || 0));

  let timerId = null;
  let finished = false;

  const tick = () => {
    if (finished) return;
    // `tick()` is also called explicitly after a hidden tab becomes visible.
    // Cancel the older scheduled callback before observing the deadline so one
    // timer can never fork into two independent callback chains.
    if (timerId !== null) {
      cancelScheduled(timerId);
      timerId = null;
    }
    const observedAtMs = now();
    const remainingMs = Math.max(0, deadlineMs - observedAtMs);
    onTick({
      startedAtMs,
      deadlineMs,
      observedAtMs,
      remainingMs,
      elapsedMs: Math.max(0, observedAtMs - startedAtMs),
      progress: deadlineMs <= startedAtMs
        ? 1
        : Math.min(1, Math.max(0, (observedAtMs - startedAtMs) / (deadlineMs - startedAtMs))),
    });
    if (remainingMs <= 0) {
      finished = true;
      timerId = null;
      onDeadline({
        startedAtMs,
        deadlineMs,
        observedAtMs,
        callbackDelayMs: Math.max(0, observedAtMs - deadlineMs),
      });
      return;
    }
    timerId = schedule(() => {
      timerId = null;
      tick();
    }, Math.min(tickIntervalMs, remainingMs));
  };

  tick();
  return {
    startedAtMs,
    deadlineMs,
    cancel() {
      if (finished) return;
      finished = true;
      if (timerId !== null) cancelScheduled(timerId);
      timerId = null;
    },
    tick,
    isFinished: () => finished,
  };
}

export function remainingWholeSeconds(remainingMs) {
  return Math.max(0, Math.ceil(Math.max(0, Number(remainingMs || 0)) / 1000));
}
