import { postJson } from '../shared/api-client.js';

const CLIENT_ID_KEY = 'study-runner-client-id';
const DEFAULT_INTERVAL_MS = 2000;

let heartbeatTimer = null;
let sequenceNumber = 0;
let cachedClientId = null;
let identityChannel = null;
const pageToken = createRandomId();
const pageStartedAt = performance.timeOrigin || Date.now();

export async function prepareStudyClientIdentity() {
  getStudyClientId();
  if (typeof BroadcastChannel !== 'function') return;
  try {
    identityChannel = new BroadcastChannel('study-runner-participant-identity');
  } catch {
    return;  // A denied channel must not prevent the study page from loading.
  }
  identityChannel.onmessage = ({ data }) => {
    if (data?.clientId !== cachedClientId || data?.pageToken === pageToken) return;
    const otherIsOlder = shouldYieldIdentity(
      { startedAt: pageStartedAt, pageToken }, data,
    );
    if (otherIsOlder) {
      cachedClientId = `study-client-${createRandomId()}`;
      try { window.sessionStorage.setItem(CLIENT_ID_KEY, cachedClientId); } catch { /* memory identity remains stable */ }
      identityChannel.postMessage({ clientId: cachedClientId, pageToken, startedAt: pageStartedAt });
    } else if (data.type === 'claim') {
      identityChannel.postMessage({ type: 'reply', clientId: cachedClientId, pageToken, startedAt: pageStartedAt });
    }
  };
  identityChannel.postMessage({ type: 'claim', clientId: cachedClientId, pageToken, startedAt: pageStartedAt });
  // A cloned tab must settle its identity before claiming a dashboard slot.
  await new Promise((resolve) => window.setTimeout(resolve, 180));
}

export function shouldYieldIdentity(local, remote) {
  return remote.startedAt < local.startedAt
    || (remote.startedAt === local.startedAt && remote.pageToken < local.pageToken);
}

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
  if (cachedClientId) return cachedClientId;
  try {
    const existing = window.sessionStorage.getItem(CLIENT_ID_KEY);
    if (existing) {
      cachedClientId = existing;
      return cachedClientId;
    }

    cachedClientId = `study-client-${createRandomId()}`;
    window.sessionStorage.setItem(CLIENT_ID_KEY, cachedClientId);
    return cachedClientId;
  } catch {
    cachedClientId ||= `study-client-${createRandomId()}`;
    return cachedClientId;
  }
}

function createRandomId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
