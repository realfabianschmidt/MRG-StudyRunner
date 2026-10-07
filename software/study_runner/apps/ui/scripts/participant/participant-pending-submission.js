// Keep the exact payload across reloads so an unconfirmed submit can be retried.
export function createPendingSubmissionStore(state, storageKey) {
  const prefix = `${storageKey}:`;
  let retrying = false;

  function load(sessionId) {
    if (state.pendingSubmission?.session_id === sessionId) return state.pendingSubmission;
    try {
      const raw = window.localStorage.getItem(`${prefix}${sessionId}`)
        || window.sessionStorage.getItem(storageKey);
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
      window.localStorage.setItem(`${prefix}${payload.session_id}`, JSON.stringify(payload));
    } catch {
      try {
        window.sessionStorage.setItem(storageKey, JSON.stringify(payload));
      } catch {
        // The current page still holds the payload for an immediate retry.
      }
    }
  }

  function clear() {
    const sessionId = state.pendingSubmission?.session_id;
    const submissionId = state.pendingSubmission?.submission_id;
    state.pendingSubmission = null;
    try {
      if (sessionId) window.localStorage.removeItem(`${prefix}${sessionId}`);
    } catch {
      // The acknowledged submission no longer needs the in-memory copy.
    }
    try {
      const fallback = JSON.parse(window.sessionStorage.getItem(storageKey));
      if (fallback?.submission_id === submissionId) {
        window.sessionStorage.removeItem(storageKey);
      }
    } catch {
      // Ignore storage failures after the server has acknowledged the commit.
    }
  }

  async function retryAll(send) {
    if (retrying) return;
    retrying = true;
    const payloads = [];
    try {
      for (let index = 0; index < window.localStorage.length; index += 1) {
        const key = window.localStorage.key(index);
        if (key?.startsWith(prefix)) {
          try {
            const payload = JSON.parse(window.localStorage.getItem(key));
            if (payload?.submission_id && payload?.session_id) payloads.push(payload);
          } catch {
            // Ignore one damaged entry and continue with other submissions.
          }
        }
      }
    } catch {
      // Session storage may still hold the fallback submission.
    }
    try {
      const legacy = window.sessionStorage.getItem(storageKey);
      if (legacy) payloads.push(JSON.parse(legacy));
    } catch {
      // Local storage entries remain retryable.
    }
    for (const payload of new Map(payloads.map((item) => [item.submission_id, item])).values()) {
      try {
        await send(payload);
        if (state.pendingSubmission?.session_id === payload.session_id) state.pendingSubmission = null;
      } catch {
        // The same immutable submission will be retried when connectivity returns.
        continue;
      }
      try { window.localStorage.removeItem(`${prefix}${payload.session_id}`); } catch {}
      try {
        const fallback = JSON.parse(window.sessionStorage.getItem(storageKey));
        if (fallback?.submission_id === payload.submission_id) {
          window.sessionStorage.removeItem(storageKey);
        }
      } catch {}
    }
    retrying = false;
  }

  function startRetry(send) {
    const retry = () => void retryAll(send);
    retry();
    window.addEventListener('online', retry);
    window.setInterval(retry, 15000);
  }

  return { load, persist, clear, retryAll, startRetry };
}
