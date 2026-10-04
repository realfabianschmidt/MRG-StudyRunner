// The exact payload is retained for an idempotent retry after a failed submit.
export function createPendingSubmissionStore(state, storageKey) {
  function load(sessionId) {
    if (state.pendingSubmission?.session_id === sessionId) return state.pendingSubmission;
    try {
      const raw = window.sessionStorage.getItem(storageKey);
      const payload = raw ? JSON.parse(raw) : null;
      if (payload?.session_id === sessionId) {
        state.pendingSubmission = payload;
        return payload;
      }
    } catch {
      // A fresh immutable payload will be prepared below.
    }
    return null;
  }

  function persist(payload) {
    state.pendingSubmission = payload;
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(payload));
    } catch {
      // The in-memory copy still makes retries in this page idempotent.
    }
  }

  function clear() {
    state.pendingSubmission = null;
    try {
      window.sessionStorage.removeItem(storageKey);
    } catch {
      // Ignore storage failures after the server has acknowledged the commit.
    }
  }

  return { load, persist, clear };
}
