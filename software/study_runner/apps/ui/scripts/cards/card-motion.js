// Motion for cards: one animation loop per element that any card can use.
//
// A card runs as a participant's question and as a live preview next to the
// study editor, so its animations must look after themselves: the loop ends
// when its element leaves the document (the card was replaced), rests while
// the element is scrolled out of view, and every loop stops on a session
// reset. The loop is kept on the element itself, never in a module variable.

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
