/**
 * The full-screen sweep between admin views.
 *
 * Layers slide in from the left until the screen is fully white, the view is
 * swapped while nothing is visible, then the layers fade out and the target
 * view's own fadeIn (main.css `.admin-view`) brings it back. Swapping under
 * cover is the point: a view change plus its first data load never shows a
 * half-built page.
 *
 * Deliberately not the View Transitions API - it is unreliable in the packaged
 * app's embedded browser - and deliberately not pure CSS, because the DOM swap
 * has to happen on the exact frame the screen is opaque, which CSS cannot
 * sequence.
 */

const SWEEP_ID = 'view-sweep';
const LAYER_COUNT = 3;
/** Safety net: a dropped transitionend must never leave a white screen behind. */
const COVER_TIMEOUT_MS = 600;
const REVEAL_MS = 260;

let activeTransition = null;

function prefersReducedMotion() {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
}

function ensureOverlay() {
  let overlay = document.getElementById(SWEEP_ID);
  if (overlay) return overlay;

  overlay = document.createElement('div');
  overlay.id = SWEEP_ID;
  overlay.className = 'view-sweep';
  overlay.hidden = true;
  // Purely decorative: it must never be announced or focusable.
  overlay.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < LAYER_COUNT; index += 1) {
    const layer = document.createElement('span');
    layer.className = 'view-sweep-layer';
    overlay.appendChild(layer);
  }
  document.body.appendChild(overlay);
  return overlay;
}

/** Resolve when the last layer has covered the screen, or when the timeout wins. */
function waitForCover(overlay) {
  return new Promise((resolve) => {
    const lastLayer = overlay.lastElementChild;
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      lastLayer?.removeEventListener('transitionend', onEnd);
      clearTimeout(timer);
      resolve();
    };
    const onEnd = (event) => {
      if (event.propertyName === 'transform') finish();
    };
    const timer = setTimeout(finish, COVER_TIMEOUT_MS);
    lastLayer?.addEventListener('transitionend', onEnd);
    if (!lastLayer) finish();
  });
}

/**
 * Run `applyChange` while the screen is covered.
 *
 * `applyChange` may be async - the whole point is that slow work (a fetch for
 * the view being opened) happens behind the cover instead of in front of the
 * operator. Its result is returned. If it throws, the overlay is still torn
 * down before the error propagates.
 */
export async function transitionToView(applyChange) {
  if (prefersReducedMotion()) {
    return applyChange?.();
  }

  // Serialize: a double click must not start a second sweep mid-flight.
  if (activeTransition) {
    try {
      await activeTransition;
    } catch {
      // A failed earlier transition must not block this one.
    }
  }

  const run = (async () => {
    const overlay = ensureOverlay();
    try {
      overlay.hidden = false;
      // Force a reflow so the layers start from their off-screen position
      // instead of being collapsed into the same frame as the class change.
      void overlay.offsetWidth;
      overlay.classList.add('is-covering');

      await waitForCover(overlay);
      return await applyChange?.();
    } finally {
      overlay.classList.remove('is-covering');
      overlay.classList.add('is-revealing');
      window.setTimeout(() => {
        overlay.classList.remove('is-revealing');
        overlay.hidden = true;
      }, REVEAL_MS);
    }
  })();

  activeTransition = run;
  try {
    return await run;
  } finally {
    if (activeTransition === run) activeTransition = null;
  }
}
