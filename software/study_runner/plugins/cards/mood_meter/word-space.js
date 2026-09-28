// The fullscreen word space: all words of the four quadrants on a pannable
// plane with momentum, larger towards the middle of the screen, the
// background crossfading to the quadrant being looked at. Used by the classic
// and the blob view. It holds no state of its own: the open overlay is found
// through the DOM, the selection is read through `isSelected`.
import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { haptic, prefersReducedMotion } from './mood-core.js';

// Hex layout constants
const CELL_W = 130, CELL_H = 96, ROWS = 5, COLS = 5;
const MARGIN_X = 180, MARGIN_Y = 140;
const QX = ((COLS - 1) * CELL_W) / 2 + MARGIN_X;
const QY = ((ROWS - 1) * CELL_H) / 2 + MARGIN_Y;

export function activeWordSpace() {
  if (typeof document === 'undefined') return null;
  return document.querySelector('#mm-overlay:not([data-closing])');
}

function buildHexPositions() {
  const pos = [];
  const offsetX = -((COLS - 1) * CELL_W + CELL_W / 2) / 2;
  const offsetY = -((ROWS - 1) * CELL_H) / 2;
  for (let r = 0; r < ROWS; r++) {
    const shiftX = (r % 2) * (CELL_W / 2);
    for (let c = 0; c < COLS; c++) {
      pos.push({ x: c * CELL_W + shiftX + offsetX, y: r * CELL_H + offsetY });
    }
  }
  return pos;
}

function updateBubbleSizes(viewport, space, panX, panY) {
  const vcx = viewport.clientWidth / 2;
  const vcy = viewport.clientHeight / 2;
  const maxDist = Math.hypot(vcx, vcy) * 1.5;

  space.querySelectorAll('.mm-word').forEach(btn => {
    const bx = parseFloat(btn.style.left) + panX;
    const by = parseFloat(btn.style.top) + panY;
    let d = Math.hypot(bx - vcx, by - vcy) / (maxDist * 0.8);
    if (d > 1.2) d = 1.2;

    const scale = Math.max(0.1, 1.65 - d * 1.15);
    const alpha = Math.max(0, 1.0 - d * 0.8);

    btn.style.setProperty('--mm-scale', scale.toFixed(3));
    btn.style.opacity = alpha.toFixed(3);
    btn.style.visibility = alpha === 0 ? 'hidden' : 'visible';
  });
}

function buildBubbleSpace(space, quads, isSelected) {
  space.replaceChildren();
  const positions = buildHexPositions();
  quads.forEach(quad => {
    const cx = quad.dirX * QX;
    const cy = quad.dirY * QY;
    quad.words.forEach((w, wi) => {
      if (wi >= positions.length) return;
      const btn = document.createElement('button');
      btn.className = 'mm-word' + (isSelected(w) ? ' mm-word--selected' : '');
      btn.dataset.word = w;
      btn.textContent = w;
      btn.style.left = (cx + positions[wi].x) + 'px';
      btn.style.top = (cy + positions[wi].y) + 'px';
      space.appendChild(btn);
    });
  });
}

function setupPan(overlay, viewport, space, initPanX, initPanY, quads, initialQuadId, onTap) {
  let panX = initPanX, panY = initPanY;
  let velX = 0, velY = 0;
  let downX = 0, downY = 0, lastX = 0, lastY = 0;
  let isPanning = false;
  let rafId = null;
  let activeQuadId = initialQuadId;

  const limit = (val, min, max) => Math.max(min, Math.min(max, val));
  const BOUND_X = QX + 300;
  const BOUND_Y = QY + 300;

  const applyTransform = () => {
    space.style.transform = `translate(${panX}px, ${panY}px)`;
    updateBubbleSizes(viewport, space, panX, panY);

    // Calculate which quadrant we are looking at
    const scX = (viewport.clientWidth / 2) - panX;
    const scY = (viewport.clientHeight / 2) - panY;
    const dirX = scX < 0 ? -1 : 1;
    const dirY = scY < 0 ? -1 : 1;

    const currentQuad = quads.find(q => q.dirX === dirX && q.dirY === dirY);
    if (currentQuad && currentQuad.id !== activeQuadId) {
      activeQuadId = currentQuad.id;
      haptic();

      // Crossfade Backgrounds
      overlay.querySelectorAll('.mm-bg').forEach(bg => {
        bg.style.opacity = bg.classList.contains(`mm-bg-${activeQuadId}`) ? '1' : '0';
      });

      // Swap Title Text
      const titleEl = overlay.querySelector('.mm-overlay-title');
      if (titleEl) {
        titleEl.style.opacity = '0';
        setTimeout(() => {
          titleEl.textContent = currentQuad.label;
          titleEl.style.opacity = '1';
        }, 150);
      }
    }
  };

  applyTransform();

  viewport.addEventListener('pointerdown', e => {
    velX = velY = 0;
    downX = lastX = e.clientX;
    downY = lastY = e.clientY;
    isPanning = true;
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    viewport.setPointerCapture(e.pointerId);
  });

  viewport.addEventListener('pointermove', e => {
    if (!isPanning) return;
    velX = e.clientX - lastX;
    velY = e.clientY - lastY;
    panX = limit(panX + velX, viewport.clientWidth / 2 - BOUND_X, viewport.clientWidth / 2 + BOUND_X);
    panY = limit(panY + velY, viewport.clientHeight / 2 - BOUND_Y, viewport.clientHeight / 2 + BOUND_Y);
    lastX = e.clientX; lastY = e.clientY;
    applyTransform();
  });

  viewport.addEventListener('pointerup', e => {
    if (!isPanning) return;
    isPanning = false;

    if (viewport.hasPointerCapture(e.pointerId)) {
      viewport.releasePointerCapture(e.pointerId);
    }

    const moved = Math.hypot(e.clientX - downX, e.clientY - downY);

    if (moved < 8) {
      // Tap: toggle word
      const target = document.elementFromPoint(e.clientX, e.clientY);
      const word = target?.closest('.mm-word');
      if (word) onTap(word.dataset.word);
    } else if (!prefersReducedMotion()) {
      // Momentum scroll
      (function momentum() {
        if (Math.abs(velX) < 0.4 && Math.abs(velY) < 0.4) return;
        velX *= 0.92; velY *= 0.92;
        panX = limit(panX + velX, viewport.clientWidth / 2 - BOUND_X, viewport.clientWidth / 2 + BOUND_X);
        panY = limit(panY + velY, viewport.clientHeight / 2 - BOUND_Y, viewport.clientHeight / 2 + BOUND_Y);
        applyTransform();
        rafId = requestAnimationFrame(momentum);
      })();
    }
  });
}

function selectedLabel(count) {
  return count > 0 ? t('cards.moodMeter.selectedCount', '{n} selected').replace('{n}', String(count)) : '';
}

/**
 * Open the word space on one quadrant.
 *   originRect   where it grows from (the tapped tile or blob)
 *   reveal       'zoom' (classic) or 'circle' (a clip-path circle from the origin)
 *   isSelected   (word) => boolean, read after every toggle
 *   onToggle     (word) => void, updates the card's state
 *   onClose      () => void, after the overlay started closing
 */
export function openWordSpace({ quads, quadId, originRect, reveal = 'zoom', isSelected, selectedCount, onToggle, onClose }) {
  if (activeWordSpace()) closeWordSpace(false);

  const initialQuad = quads.find(qd => qd.id === quadId) || quads[0];
  const overlay = document.createElement('div');
  overlay.id = 'mm-overlay';
  overlay.style.background = '#111'; // Base dark space

  // Render 4 large background gradients that we crossfade
  const backgrounds = quads.map(qd => `
    <div class="mm-bg mm-bg-${qd.id}"
         style="position:absolute;inset:0;background:linear-gradient(145deg, ${qd.color} 0%, ${qd.colorDark} 100%);
         opacity:${qd.id === initialQuad.id ? '1' : '0'};transition:opacity 0.8s ease;"></div>
  `).join('');

  overlay.innerHTML = `
    ${backgrounds}
    <button class="mm-back-btn" aria-label="${escapeHtml(t('cards.moodMeter.back', 'Back to overview'))}">
      <i class="iconoir-arrow-left"></i>
    </button>
    <div class="mm-float-header">
      <span class="mm-overlay-title" style="transition: opacity 0.3s ease;">${escapeHtml(initialQuad.label)}</span>
      <span class="mm-sel-counter">${escapeHtml(selectedLabel(selectedCount()))}</span>
    </div>
    <div id="mm-bubble-viewport"><div id="mm-bubble-space"></div></div>`;

  overlay._onClose = onClose;
  overlay._originRect = originRect;
  overlay._reveal = reveal;

  const cx = originRect.left + originRect.width / 2;
  const cy = originRect.top + originRect.height / 2;
  const reduced = prefersReducedMotion();
  if (reduced) {
    overlay.style.opacity = '0';
  } else if (reveal === 'circle') {
    overlay.style.clipPath = `circle(${Math.max(originRect.width, originRect.height) / 2}px at ${cx}px ${cy}px)`;
  } else {
    // Zoom-in from origin button centre
    overlay.style.transformOrigin = `${cx}px ${cy}px`;
    overlay.style.transform = 'scale(0.06)';
    overlay.style.opacity = '0';
  }

  document.body.appendChild(overlay);

  const viewport = overlay.querySelector('#mm-bubble-viewport');
  const space = overlay.querySelector('#mm-bubble-space');
  buildBubbleSpace(space, quads, isSelected);

  // Center the viewport exactly on the quadrant that was tapped
  const initPanX = (viewport.clientWidth / 2) - (initialQuad.dirX * QX);
  const initPanY = (viewport.clientHeight / 2) - (initialQuad.dirY * QY);

  setupPan(overlay, viewport, space, initPanX, initPanY, quads, initialQuad.id, (word) => {
    onToggle(word);
    space.querySelectorAll('.mm-word').forEach(btn => {
      btn.classList.toggle('mm-word--selected', isSelected(btn.dataset.word));
    });
    const counter = overlay.querySelector('.mm-sel-counter');
    if (counter) counter.textContent = selectedLabel(selectedCount());
  });

  overlay.querySelector('.mm-back-btn').addEventListener('click', () => closeWordSpace(true));
  overlay._escHandler = ev => { if (ev.key === 'Escape') closeWordSpace(true); };
  document.addEventListener('keydown', overlay._escHandler);

  requestAnimationFrame(() => {
    if (reduced) {
      overlay.style.transition = 'opacity 0.2s ease';
      overlay.style.opacity = '1';
    } else if (reveal === 'circle') {
      const radius = Math.hypot(window.innerWidth, window.innerHeight);
      overlay.style.transition = 'clip-path 0.55s cubic-bezier(.16,.85,.20,1)';
      overlay.style.clipPath = `circle(${radius}px at ${cx}px ${cy}px)`;
    } else {
      overlay.style.transition = 'transform 0.42s cubic-bezier(.16,.85,.20,1), opacity 0.28s ease';
      overlay.style.transform = 'scale(1)';
      overlay.style.opacity = '1';
    }
  });
}

export function closeWordSpace(animate) {
  const overlay = activeWordSpace();
  if (!overlay) return;
  overlay.dataset.closing = 'true';

  if (overlay._escHandler) document.removeEventListener('keydown', overlay._escHandler);
  overlay._onClose?.();

  if (!animate || prefersReducedMotion()) { overlay.remove(); return; }

  const rect = overlay._originRect;
  if (overlay._reveal === 'circle' && rect) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    overlay.style.transition = 'clip-path 0.4s cubic-bezier(.4,0,.8,1)';
    overlay.style.clipPath = `circle(${Math.max(rect.width, rect.height) / 2}px at ${cx}px ${cy}px)`;
    overlay.addEventListener('transitionend', () => overlay.remove(), { once: true });
    return;
  }
  overlay.style.transition = 'transform 0.3s cubic-bezier(.4,0,.8,1), opacity 0.22s ease';
  overlay.style.transform = 'scale(0.06)';
  overlay.style.opacity = '0';
  overlay.addEventListener('transitionend', () => overlay.remove(), { once: true });
}
