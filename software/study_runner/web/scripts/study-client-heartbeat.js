import { postJson } from './api-client.js';

const CLIENT_ID_KEY = 'study-runner-client-id';
const DEFAULT_INTERVAL_MS = 2000;

let heartbeatTimer = null;
let sequenceNumber = 0;

export function startStudyClientHeartbeat(getPayload, options = {}) {
  stopStudyClientHeartbeat();

  const intervalMs = Math.max(1000, Number(options.intervalMs || DEFAULT_INTERVAL_MS));
  const onHeartbeat = typeof options.onHeartbeat === 'function' ? options.onHeartbeat : () => {};

  const sendHeartbeat = () => {
    void postJson('/api/study-client/heartbeat', {
      client_id: getStudyClientId(),
      client_timestamp: new Date().toISOString(),
      sequence_number: sequenceNumber,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
      },
      ...safePayload(getPayload),
    })
      .then((response) => onHeartbeat(response || {}))
      .catch((error) => {
        console.debug('[study] Heartbeat failed:', error);
      });
    sequenceNumber += 1;
  };

  sendHeartbeat();
  heartbeatTimer = window.setInterval(sendHeartbeat, intervalMs);
  return stopStudyClientHeartbeat;
}

export function stopStudyClientHeartbeat() {
  if (heartbeatTimer !== null) {
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function safePayload(getPayload) {
  if (typeof getPayload !== 'function') {
    return {};
  }
  try {
    return getPayload() || {};
  } catch {
    return {};
  }
}

export function getStudyClientId() {
  try {
    const existing = window.sessionStorage.getItem(CLIENT_ID_KEY);
    if (existing) {
      return existing;
    }

    const created = `study-client-${createRandomId()}`;
    window.sessionStorage.setItem(CLIENT_ID_KEY, created);
    return created;
  } catch {
    return `study-client-${createRandomId()}`;
  }
}

function createRandomId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
