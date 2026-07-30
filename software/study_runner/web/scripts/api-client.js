function isPositiveFiniteNumber(value) {
  return Number.isFinite(Number(value)) && Number(value) > 0;
}

function combineAbortSignals(signals) {
  const usableSignals = signals.filter(Boolean);
  if (usableSignals.length <= 1) {
    return {
      signal: usableSignals[0] || undefined,
      cleanup: () => {},
    };
  }

  const controller = new AbortController();
  const abort = (event) => {
    if (!controller.signal.aborted) {
      controller.abort(event?.target?.reason);
    }
  };

  usableSignals.forEach((signal) => {
    if (signal.aborted) {
      abort({ target: signal });
    } else {
      signal.addEventListener('abort', abort, { once: true });
    }
  });

  return {
    signal: controller.signal,
    cleanup: () => usableSignals.forEach((signal) => signal.removeEventListener('abort', abort)),
  };
}

export async function requestJson(url, options = {}) {
  const { timeoutMs, signal, ...fetchOptions } = options;
  const timeoutController = isPositiveFiniteNumber(timeoutMs) ? new AbortController() : null;
  const combined = combineAbortSignals([signal, timeoutController?.signal]);
  let timeoutId = null;
  let didTimeout = false;

  if (timeoutController) {
    timeoutId = window.setTimeout(() => {
      didTimeout = true;
      timeoutController.abort();
    }, Number(timeoutMs));
  }

  let response;
  try {
    response = await fetch(url, { ...fetchOptions, signal: combined.signal });
  } catch (error) {
    if (didTimeout) {
      throw new Error(`Request timed out after ${Number(timeoutMs)} ms`);
    }
    throw error;
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId);
    }
    combined.cleanup();
  }
  const contentType = response.headers.get('content-type') || '';
  const isJson = contentType.includes('application/json');
  const body = isJson ? await response.json() : null;

  if (!response.ok) {
    const message = body?.error || `Request failed with status ${response.status}`;
    throw new Error(message);
  }

  if (!isJson) {
    return null;
  }

  return body;
}

export function getJson(url, options = {}) {
  return requestJson(url, options);
}

export function postJson(url, payload, options = {}) {
  const headers = {
    ...(options.headers || {}),
    'Content-Type': 'application/json',
  };
  return requestJson(url, {
    ...options,
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });
}
