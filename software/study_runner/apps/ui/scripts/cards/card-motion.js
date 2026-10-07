// Motion for cards: one animation loop per element that any card can use,
// and the tapping finger that says "touch here" until the card is touched.
//
// A card runs as a participant's question and as a live preview next to the
// study editor, so its animations must look after themselves: the loop ends
// when its element leaves the document (the card was replaced), rests while
// the element is scrolled out of view, and every loop stops on a session
// reset. The loop is kept on the element itself, never in a module variable.

import { escapeHtml } from '../shared/dom-utils.js';

export function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Run tick(timeSeconds, dtSeconds) every frame while `element` is in the
 * document and in view. tick returns false to rest until wake() is called.
 * Returns { wake, stop }, also kept as element._cardAnimation.
 */
export function runAnimation(element, tick) {
  let frame = null;
  let last = null;
  let visible = true;
  let resting = false;

  const loop = (now) => {
    frame = null;
    if (!element.isConnected) {
      controls.stop();
      return;
    }
    const dt = last === null ? 1 / 60 : Math.min(0.05, (now - last) / 1000);
    last = now;
    if (tick(now / 1000, dt) === false) {
      resting = true;
      return;
    }
    if (visible) frame = requestAnimationFrame(loop);
  };

  // Cards are bound before they are attached to the page, so the first frame
  // is scheduled regardless; the loop ends once the element is gone.
  const schedule = () => {
    if (frame === null) {
      last = null;
      frame = requestAnimationFrame(loop);
    }
  };

  // Rest while scrolled out of view (a long list of preview cards, a card
  // further down the study); pick up again when it comes back.
  const observer = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver((entries) => {
      visible = entries.some((entry) => entry.isIntersecting);
      if (visible && !resting) schedule();
    })
    : null;
  observer?.observe(element);

  const controls = {
    wake() {
      resting = false;
      if (visible) schedule();
    },
    stop() {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      observer?.disconnect();
      delete element.dataset.cardAnimated;
    },
  };
  element._cardAnimation?.stop();
  element._cardAnimation = controls;
  element.dataset.cardAnimated = 'true';
  controls.wake();
  return controls;
}

export function stopAllAnimations(root = typeof document !== 'undefined' ? document : null) {
  root?.querySelectorAll?.('[data-card-animated]').forEach((element) => element._cardAnimation?.stop());
}

/**
 * A gently tapping finger instead of a "touch here" sentence. The sentence
 * stays the element's accessible name, so screen readers still read it. The
 * first touch on the card takes the finger away (armTouchHints, which
 * mountCard runs for every card); it keeps still for reduce-motion (main.css).
 * `attrs` carries the card's own class/id for positioning.
 */
export function touchHint({ label, tag = 'span', attrs = '' }) {
  return `<${tag} ${attrs} role="img" aria-label="${escapeHtml(label)}" data-touch-hint><i class="iconoir-one-finger-select-hand-gesture touch-hint" aria-hidden="true"></i></${tag}>`;
}

function hideTouchHints(event) {
  event.currentTarget.querySelectorAll('[data-touch-hint]').forEach((hint) => { hint.dataset.touchHint = 'done'; });
}

// The same listener is never added twice, so mounting a card again into the
// same element (the editor preview) keeps a single one.
export function armTouchHints(element) {
  element.addEventListener('pointerdown', hideTouchHints, true);
}
