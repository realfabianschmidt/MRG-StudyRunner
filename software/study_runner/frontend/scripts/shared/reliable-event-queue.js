import { postJson } from './api-client.js';

const STORAGE_KEY = 'study-runner-pending-events-v3';
const LEGACY_STORAGE_KEY = 'study-runner-pending-events-v2';
const MAX_PENDING_EVENTS = 500;
let drainPromise = null;
let deliveryTail = Promise.resolve();
const deliveredResponses = new Map();

/** Queue before sending so reloads and temporary disconnects preserve source time. */
export async function sendReliableStudyEvent(endpoint, payload, options = {}) {
  const event = normalizeEvent(endpoint, payload, options);
  const alreadyDelivered = deliveredResponseFor(event);
  if (alreadyDelivered.found) return alreadyDelivered.response;
  const pending = readQueue();
  const existing = pending.find((item) => item.payload.event_id === event.payload.event_id);
  if (existing && eventSignature(existing) !== eventSignature(event)) {
    throw new Error(`Study event id ${event.payload.event_id} was reused with different data.`);
  }
  if (!existing) {
    if (pending.length >= MAX_PENDING_EVENTS) {
      throw new Error('The reliable study-event queue is full.');
    }
    pending.push(event);
    writeQueue(pending);
  }
  return scheduleDelivery(async () => {
    const cached = deliveredResponseFor(event);
    if (cached.found) return cached.response;
    await drainQueue();
    return deliveredResponseFor(event).response;
  });
}

export function flushReliableStudyEvents() {
  if (drainPromise) return drainPromise;
  drainPromise = scheduleDelivery(drainQueue)
    .catch(() => undefined)
    .finally(() => {
      drainPromise = null;
    });
  return drainPromise;
}

export function createEventId(prefix = 'event') {
  const randomId = window.crypto?.randomUUID
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${randomId}`;
}

async function drainQueue() {
  while (true) {
    const event = readQueue()[0];
    if (!event) return;
    // One global delivery chain covers both immediate sends and later flushes.
    // A stop can therefore never overtake its still-pending start event.
    const response = await deliver(event);
    rememberDeliveredResponse(event, response);
    removeEvent(event.payload.event_id);
  }
}

function scheduleDelivery(operation) {
  const run = deliveryTail.then(operation);
  // A failed request rejects its caller but must not poison every later retry.
  deliveryTail = run.catch(() => undefined);
  return run;
}

function normalizeEvent(endpoint, payload, options) {
  const normalizedEndpoint = String(endpoint || '').trim();
  if (!normalizedEndpoint.startsWith('/api/')) {
    throw new Error('Reliable study events require a local /api/ endpoint.');
  }
  const normalizedPayload = { ...(payload || {}) };
  normalizedPayload.event_id = String(normalizedPayload.event_id || createEventId()).trim();
  if (!normalizedPayload.event_id) {
    throw new Error('Reliable study events require an event_id.');
  }
  return {
    endpoint: normalizedEndpoint,
    payload: normalizedPayload,
    timeout_ms: Math.max(250, Number(options.timeoutMs || 1500)),
    queued_at: new Date().toISOString(),
  };
}

function deliver(event) {
  return postJson(event.endpoint, event.payload, { timeoutMs: event.timeout_ms });
}

function removeEvent(eventId) {
  writeQueue(readQueue().filter((event) => event.payload?.event_id !== eventId));
}

function readQueue() {
  const durable = readStoredQueue(getStorage('localStorage'), STORAGE_KEY);
  const currentFallback = readStoredQueue(getStorage('sessionStorage'), STORAGE_KEY);
  const legacy = readStoredQueue(getStorage('sessionStorage'), LEGACY_STORAGE_KEY);
  const deduplicated = new Map();
  [...legacy, ...currentFallback, ...durable].forEach((event) => {
    if (event?.payload?.event_id && !deduplicated.has(event.payload.event_id)) {
      deduplicated.set(event.payload.event_id, event);
    }
  });
  const events = [...deduplicated.values()].slice(0, MAX_PENDING_EVENTS);
  if (legacy.length || currentFallback.length) {
    writeQueue(events);
    removeStorageKey(getStorage('sessionStorage'), LEGACY_STORAGE_KEY);
  }
  return events;
}

function writeQueue(events) {
  const encoded = JSON.stringify(events);
  const durable = getStorage('localStorage');
  if (writeStoredQueue(durable, encoded, events.length)) {
    removeStorageKey(getStorage('sessionStorage'), STORAGE_KEY);
    return;
  }
  // Safari private mode and locked-down tablets may reject localStorage. The
  // tab-scoped fallback still protects reloads and keeps immediate delivery.
  writeStoredQueue(getStorage('sessionStorage'), encoded, events.length);
}

function getStorage(name) {
  try {
    return window?.[name] || null;
  } catch {
    return null;
  }
}

function readStoredQueue(storage, key) {
  if (!storage) return [];
  try {
    const parsed = JSON.parse(storage.getItem(key) || '[]');
    return Array.isArray(parsed)
      ? parsed.filter((event) => event && event.endpoint && event.payload?.event_id)
      : [];
  } catch {
    return [];
  }
}

function writeStoredQueue(storage, encoded, hasEvents) {
  if (!storage) return false;
  try {
    if (hasEvents) storage.setItem(STORAGE_KEY, encoded);
    else storage.removeItem(STORAGE_KEY);
    return true;
  } catch {
    return false;
  }
}

function removeStorageKey(storage, key) {
  try {
    storage?.removeItem(key);
  } catch {
    // Storage cleanup is best effort; event delivery remains idempotent.
  }
}

function rememberDeliveredResponse(event, response) {
  deliveredResponses.set(event.payload.event_id, {
    signature: eventSignature(event),
    response,
  });
  while (deliveredResponses.size > MAX_PENDING_EVENTS) {
    deliveredResponses.delete(deliveredResponses.keys().next().value);
  }
}

function deliveredResponseFor(event) {
  const delivered = deliveredResponses.get(event.payload.event_id);
  if (!delivered) return { found: false, response: undefined };
  if (delivered.signature !== eventSignature(event)) {
    throw new Error(`Study event id ${event.payload.event_id} was reused with different data.`);
  }
  return { found: true, response: delivered.response };
}

function eventSignature(event) {
  return JSON.stringify([event.endpoint, event.payload]);
}
