// One tablet-to-server clock estimate is shared by cards, stimuli, and camera frames.
// The chosen four-timestamp exchange stays paired with its own network delay.
export const CLOCK_SYNC_MAX_AGE_MS = 120_000;
export const CLOCK_SYNC_INTERVAL_MS = 60_000;

export function createTabletClock({ exchange, now = () => performance.now(), pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), onSelected = () => {} }) {
  let selected = null;
  let inFlight = null;
  let generation = 0;

  async function sync() {
    if (inFlight) return inFlight;
    inFlight = (async () => {
      const candidates = [];
      for (let round = 0; round < 3; round += 1) {
        const clientSendMs = now();
        try {
          const reply = await exchange(clientSendMs);
          const clientReceiveMs = now();
          // Number(null) is zero; a malformed reply must never become a
          // plausible clock sample just because the network call resolved.
          const serverReceiveMs = typeof reply?.server_receive_ms === 'number' ? reply.server_receive_ms : NaN;
          const serverSendMs = typeof reply?.server_send_ms === 'number' ? reply.server_send_ms : NaN;
          const serverWorkMs = serverSendMs - serverReceiveMs;
          const networkDelayMs = (clientReceiveMs - clientSendMs) - serverWorkMs;
          const offsetMs = ((serverReceiveMs - clientSendMs) + (serverSendMs - clientReceiveMs)) / 2;
          if ([clientSendMs, clientReceiveMs, serverReceiveMs, serverSendMs, networkDelayMs, offsetMs]
            .every(Number.isFinite) && serverWorkMs >= 0 && networkDelayMs >= 0) {
            candidates.push({ client_send_ms: clientSendMs, client_receive_ms: clientReceiveMs,
              server_receive_ms: serverReceiveMs, server_send_ms: serverSendMs,
              network_delay_ms: networkDelayMs, offset_ms: offsetMs });
          }
        } catch {
          // An unavailable server leaves the last estimate to expire naturally.
        }
        if (round < 2) await pause(100);
      }
      if (candidates.length) {
        candidates.sort((left, right) => left.network_delay_ms - right.network_delay_ms);
        const choice = candidates[0];
        selected = { ...choice, id: `${Math.round(choice.server_receive_ms)}-${++generation}` };
        onSelected({ ...selected });
      }
      return selected ? { ...selected } : null;
    })().finally(() => { inFlight = null; });
    return inFlight;
  }

  function evidence(clientMonotonicMs = now()) {
    const ageMs = selected ? Math.max(0, clientMonotonicMs - selected.client_receive_ms) : null;
    const fresh = selected && Number.isFinite(clientMonotonicMs)
      && clientMonotonicMs >= selected.client_send_ms
      && ageMs <= CLOCK_SYNC_MAX_AGE_MS;
    return {
      source_epoch_ms: fresh ? clientMonotonicMs + selected.offset_ms : null,
      clock_sync_id: fresh ? selected.id : null,
      clock_sync_age_ms: fresh ? ageMs : null,
      clock_sync_rtt_ms: fresh ? selected.network_delay_ms : null,
      time_source: fresh ? 'tablet_sync' : 'server_receipt',
    };
  }

  return { sync, evidence, isFresh: () => evidence().time_source === 'tablet_sync' };
}
