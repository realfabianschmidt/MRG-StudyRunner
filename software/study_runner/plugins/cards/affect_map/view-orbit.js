// Orbit view: the circumplex of affect (Russell, 1980) drawn as a wheel, the
// way the Geneva Emotion Wheel (Scherer, 2005) is read: the angle is the kind
// of feeling, the distance from the middle its intensity. Every word sits at
// its own place, drifting slowly on its orbit; where the participant touches,
// nearby words grow and show their label (fisheye), far ones fade. Records
// words + position + intensity (distance from the middle).
import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { prefersReducedMotion, runAnimation } from '/static/scripts/cards/card-motion.js';
import {
  QUADRANTS,
  localizedQuadrants,
  colorAt,
  createSpring,
  haptic,
  orbitPlace,
  quadrantAt,
  wordCoordinates,
} from './mood-core.js';

export const records = 'words+position';

const TAP_SLOP = 10;
// Fisheye (after Sarkar & Brown): words within this share of the radius
// around the finger are pushed apart, the nearest few show their label.
const LENS_RADIUS = 0.45;
const LENS_STRENGTH = 2.4;
const LABELLED = 6;

function ringGradient(color = colorAt) {
  // conic-gradient starts at the top and runs clockwise: yellow top right,
  // green bottom right, blue bottom left, red top left.
  const stops = [
    [0, color(0.5, 1)], [45, color(1, 1)], [90, color(1, 0.5)], [135, color(1, 0)],
    [180, color(0.5, 0)], [225, color(0, 0)], [270, color(0, 0.5)], [315, color(0, 1)], [360, color(0.5, 1)],
  ];
  return `conic-gradient(${stops.map(([angle, color]) => `${color} ${angle}deg`).join(', ')})`;
}

export function render(_q, i, ctx) {
  const color = (p, e) => ctx.colorAt?.(i, p, e) || colorAt(p, e);
  const words = wordCoordinates(ctx.quads(i)).map((entry) => {
    const place = orbitPlace(entry);
    return `
      <button type="button" class="mm-orbit-word" data-card-index="${i}" data-word="${escapeHtml(entry.word)}"
              data-x="${place.x.toFixed(4)}" data-y="${place.y.toFixed(4)}" data-radius="${place.radius.toFixed(3)}"
              style="--mm-word-color:${color(entry.pleasantness, entry.energy)};--x:${place.x.toFixed(4)};--y:${place.y.toFixed(4)};">
        <span class="mm-orbit-label">${escapeHtml(entry.word)}</span>
      </button>`;
  }).join('');
  const quadrantLabels = localizedQuadrants(t).map((quadrant) => (
    `<span class="mm-orbit-quadrant mm-orbit-quadrant--${quadrant.id}">${escapeHtml(quadrant.label)}</span>`
  )).join('');
  return `
    <div class="mm-orbit" id="mm-orbit-${i}" data-card-index="${i}">
      <div class="mm-orbit-ring" style="--mm-ring:${ringGradient(color)};" aria-hidden="true"></div>
      <div class="mm-orbit-orbits" aria-hidden="true"><span></span><span></span><span></span></div>
      ${quadrantLabels}
      <div class="mm-orbit-words">${words}</div>
      <span class="mm-orbit-dot" aria-hidden="true"></span>
      <p class="mm-orbit-hint">${escapeHtml(t('cards.moodMeter.orbitHint', 'Touch the wheel where you are, then tap your words'))}</p>
    </div>`;
}

export function bind(cardElement, i, ctx) {
  const wheel = cardElement.querySelector(`#mm-orbit-${i}`);
  if (!wheel) return;
  const dot = wheel.querySelector('.mm-orbit-dot');
  const words = [...wheel.querySelectorAll('.mm-orbit-word')].map((element) => ({
    element,
    x: Number(element.dataset.x),
    y: Number(element.dataset.y),
    radius: Number(element.dataset.radius),
  }));
  const reduced = prefersReducedMotion();
  const focus = createSpring({ x: 0, y: 0 }, { stiffness: 190 });
  let active = false;
  let down = null;
  let quadrant = null;
  let measuredDiameter = 0;

  const draw = (time) => {
    const size = wheel.clientWidth / 2;
    if (!size) return;
    if (measuredDiameter !== size * 2) {
      measuredDiameter = size * 2;
      words.forEach((word) => { word.nearSize = null; });
    }
    const selected = ctx.state(i).selected;
    const focusX = focus.value.x * size;
    const focusY = focus.value.y * size;
    const lens = LENS_RADIUS * size;
    dot.style.transform = `translate(${focusX}px, ${focusY}px)`;

    const placed = words.map((word) => {
      // A slow sway along each orbit; inner orbits move a little more.
      const sway = reduced ? 0 : 0.05 * Math.sin(time * 0.22 + word.radius * 9) * (1.2 - word.radius);
      let x = (word.x * Math.cos(sway) - word.y * Math.sin(sway)) * size;
      let y = (word.x * Math.sin(sway) + word.y * Math.cos(sway)) * size;
      let distance = Infinity;
      if (active) {
        const dx = x - focusX;
        const dy = y - focusY;
        distance = Math.hypot(dx, dy);
        if (distance > 0 && distance < lens) {
          const t = distance / lens;
          const spread = ((LENS_STRENGTH + 1) * t) / (LENS_STRENGTH * t + 1);
          x = focusX + (dx / distance) * spread * lens;
          y = focusY + (dy / distance) * spread * lens;
        }
      }
      return { word, x, y, distance };
    });
    const labelled = new Set(
      [...placed].filter((entry) => entry.distance < lens).sort((a, b) => a.distance - b.distance)
        .slice(0, LABELLED).map((entry) => entry.word),
    );

    for (const { word, x, y, distance } of placed) {
      const near = labelled.has(word);
      const isSelected = selected.has(word.element.dataset.word);
      const closeness = Number.isFinite(distance) ? Math.max(0, 1 - distance / (lens * 1.6)) : 0;
      const scale = near ? 1 : (isSelected ? 1 : 0.6 + 0.35 * closeness);
      word.element.classList.toggle('is-near', near);
      word.element.classList.toggle('is-selected', isSelected);
      if (word.wasSelected !== isSelected) word.nearSize = null;
      word.wasSelected = isSelected;
      if (near && !word.nearSize) {
        word.nearSize = { width: word.element.offsetWidth / 2, height: word.element.offsetHeight / 2 };
      }
      const halfWidth = near ? word.nearSize.width : 7;
      const halfHeight = near ? word.nearSize.height : 7;
      // Clamp only the displayed label; recorded pointer coordinates stay untouched.
      const visibleX = Math.max(-size + halfWidth, Math.min(size - halfWidth, x));
      const visibleY = Math.max(-size + halfHeight, Math.min(size - halfHeight, y));
      word.element.style.transform = `translate(calc(${visibleX.toFixed(1)}px - 50%), calc(${visibleY.toFixed(1)}px - 50%)) scale(${scale.toFixed(3)})`;
    }
  };

  const animation = runAnimation(wheel, (time, dt) => {
    if (wheel.offsetParent === null) return true; // card not shown: idle cheaply
    const moving = focus.step(dt);
    draw(time);
    return !reduced || moving;
  });

  const pointFrom = (event) => {
    const rect = wheel.getBoundingClientRect();
    let x = (event.clientX - rect.left - rect.width / 2) / (rect.width / 2);
    let y = (event.clientY - rect.top - rect.height / 2) / (rect.height / 2);
    const length = Math.hypot(x, y);
    if (length > 1) { x /= length; y /= length; }
    return { x, y };
  };

  const moveTo = (point) => {
    focus.target = point;
    if (reduced) focus.value = { ...point };
    const next = quadrantAt(0.5 + point.x / 2, 0.5 - point.y / 2);
    if (quadrant && next !== quadrant) haptic();
    quadrant = next;
    animation.wake();
  };

  wheel.addEventListener('pointerdown', (event) => {
    active = true;
    // A tap picks a word only if it already showed its label before this
    // touch began; the first touch somewhere new just looks around.
    const touched = document.elementFromPoint(event.clientX, event.clientY)?.closest('.mm-orbit-word');
    const pickable = touched && (touched.classList.contains('is-near') || touched.classList.contains('is-selected'));
    down = { x: event.clientX, y: event.clientY, word: pickable ? touched : null };
    wheel.classList.add('mm-orbit--active');
    wheel.setPointerCapture(event.pointerId);
    moveTo(pointFrom(event));
  });
  wheel.addEventListener('pointermove', (event) => {
    if (down) moveTo(pointFrom(event));
  });
  const release = (event) => {
    if (!down) return;
    const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
    const pickedWord = down.word;
    down = null;
    if (wheel.hasPointerCapture(event.pointerId)) wheel.releasePointerCapture(event.pointerId);
    const point = pointFrom(event);
    moveTo(point);
    ctx.setPosition(i, {
      pleasantness: 0.5 + point.x / 2,
      energy: 0.5 - point.y / 2,
      intensity: Math.min(1, Math.hypot(point.x, point.y)),
    });
    if (moved < TAP_SLOP && event.type === 'pointerup' && pickedWord && wheel.contains(pickedWord)) {
      ctx.toggle(i, pickedWord.dataset.word);
    }
    animation.wake();
  };
  wheel.addEventListener('pointerup', release);
  wheel.addEventListener('pointercancel', release);
}

export function refresh(i) {
  // The next frame redraws selection state; make sure one comes.
  document.getElementById(`mm-orbit-${i}`)?._cardAnimation?.wake();
}
