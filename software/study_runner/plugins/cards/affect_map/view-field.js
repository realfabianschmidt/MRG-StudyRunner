// Field view: the Affect Grid (Russell, Weiss & Mendelsohn, 1989) as a living
// color field. The participant drags a glowing orb to where they are on
// pleasantness (left -> right) x energy (bottom -> top); the orb's shape
// follows Apple's State of Mind and How We Feel -- jagged when tense, a
// flower when elated, round when calm, hanging when low. On release the words
// closest to that point bloom around the orb. Records words + position.
import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { prefersReducedMotion, runAnimation } from '/static/scripts/cards/card-motion.js';
import {
  QUADRANTS,
  blobPath,
  clamp01,
  colorAt,
  createSpring,
  haptic,
  nearestWords,
  quadrantAt,
  shapeAt,
  wordCoordinates,
} from './mood-core.js';

export const records = 'words+position';

const ORB_BOX = 160;
const ORB_RADIUS = 42;
const BLOOM_COUNT = 8;
const BLOOM_RADIUS = 112;

export function render(_q, i, ctx) {
  const clouds = (ctx?.quads?.(i) || QUADRANTS).map((quadrant) => (
    `<span class="mm-cloud mm-cloud--${quadrant.id}" style="--mm-cloud:${quadrant.color};"></span>`
  )).join('');
  return `
    <div class="mm-field" id="mm-field-${i}" data-card-index="${i}">
      <div class="mm-field-clouds" aria-hidden="true">${clouds}</div>
      <div class="mm-field-cross" aria-hidden="true"></div>
      <span class="mm-field-axis mm-field-axis--energy">${escapeHtml(t('cards.moodMeter.axisEnergy', 'more energy'))}</span>
      <span class="mm-field-axis mm-field-axis--pleasant">${escapeHtml(t('cards.moodMeter.axisPleasant', 'more pleasant'))}</span>
      <p class="mm-field-hint">${escapeHtml(t('cards.moodMeter.fieldHint', 'Drag the light to where you are right now'))}</p>
      <svg class="mm-orb mm-orb--idle" width="${ORB_BOX}" height="${ORB_BOX}" viewBox="0 0 ${ORB_BOX} ${ORB_BOX}" aria-hidden="true" focusable="false">
        <path class="mm-orb-shape" d="${blobPath(ORB_BOX / 2, ORB_BOX / 2, ORB_RADIUS, shapeAt(0.5, 0.5))}"></path>
      </svg>
      <div class="mm-field-words"></div>
    </div>`;
}

export function bind(cardElement, i, ctx) {
  const field = cardElement.querySelector(`#mm-field-${i}`);
  if (!field) return;
  const orb = field.querySelector('.mm-orb');
  const shape = field.querySelector('.mm-orb-shape');
  const reduced = prefersReducedMotion();
  const spring = createSpring({ x: 0.5, y: 0.5 }, { stiffness: 140 });
  let dragging = false;
  let quadrant = null;

  const draw = (time) => {
    const rect = field.getBoundingClientRect();
    const { x: p, y: e } = spring.value;
    orb.style.transform = `translate(${p * rect.width - ORB_BOX / 2}px, ${(1 - e) * rect.height - ORB_BOX / 2}px)`;
    shape.setAttribute('d', blobPath(ORB_BOX / 2, ORB_BOX / 2, ORB_RADIUS * (dragging ? 1.12 : 1), shapeAt(p, e), reduced ? 0 : time));
    const color = ctx.colorAt?.(i, p, e) || colorAt(p, e);
    shape.style.fill = color;
    orb.style.setProperty('--mm-orb-glow', color);
  };

  const animation = reduced ? null : runAnimation(field, (time, dt) => {
    if (field.offsetParent === null) return true; // card not shown: idle cheaply
    spring.step(dt);
    draw(time);
    return true;
  });

  const pointFrom = (event) => {
    const rect = field.getBoundingClientRect();
    return {
      x: clamp01((event.clientX - rect.left) / rect.width),
      y: clamp01(1 - (event.clientY - rect.top) / rect.height),
    };
  };

  const moveTo = (point) => {
    spring.target = point;
    if (reduced) {
      spring.value = { ...point };
      draw(0);
    }
    const next = quadrantAt(point.x, point.y);
    if (quadrant && next !== quadrant) haptic();
    quadrant = next;
  };

  field.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.mm-bloom-word')) return;
    dragging = true;
    field.setPointerCapture(event.pointerId);
    field.classList.add('mm-field--dragging');
    orb.classList.remove('mm-orb--idle');
    collapseWords(field);
    moveTo(pointFrom(event));
    animation?.wake();
  });
  field.addEventListener('pointermove', (event) => {
    if (dragging) moveTo(pointFrom(event));
  });
  const release = (event) => {
    if (!dragging) return;
    dragging = false;
    if (field.hasPointerCapture(event.pointerId)) field.releasePointerCapture(event.pointerId);
    field.classList.remove('mm-field--dragging');
    const point = pointFrom(event);
    moveTo(point);
    ctx.setPosition(i, { pleasantness: point.x, energy: point.y });
    bloomWords(field, i, ctx, point);
  };
  field.addEventListener('pointerup', release);
  field.addEventListener('pointercancel', release);

  // Bound before the card is attached: draw once it has a size.
  if (reduced) requestAnimationFrame(() => draw(0));
}

function collapseWords(field) {
  field.querySelectorAll('.mm-bloom-word').forEach((word) => {
    word.classList.remove('is-in');
    word.addEventListener('transitionend', () => word.remove(), { once: true });
    if (prefersReducedMotion()) word.remove();
  });
}

// Place the nearest words around the orb, each in the direction of its own
// spot on the plane; then let them push each other (and the orb) apart until
// nothing overlaps, staying inside the field.
function bloomWords(field, i, ctx, point) {
  const layer = field.querySelector('.mm-field-words');
  if (!layer) return;
  const rect = field.getBoundingClientRect();
  const orb = { x: point.x * rect.width, y: (1 - point.y) * rect.height };
  const words = nearestWords(wordCoordinates(ctx.quads(i)), point.x, point.y, BLOOM_COUNT);

  layer.replaceChildren();
  const selected = ctx.state(i).selected;
  const boxes = words.map((entry, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `mm-bloom-word${selected.has(entry.word) ? ' is-selected' : ''}`;
    button.dataset.cardIndex = String(i);
    button.dataset.word = entry.word;
    button.textContent = entry.word;
    button.style.transitionDelay = `${index * 35}ms`;
    button.style.setProperty('--mm-word-color', ctx.colorAt?.(i, entry.pleasantness, entry.energy) || colorAt(entry.pleasantness, entry.energy));
    layer.appendChild(button);
    const angle = Math.atan2(-(entry.energy - point.y) * rect.height, (entry.pleasantness - point.x) * rect.width + 0.0001);
    return {
      button,
      w: button.offsetWidth + 8,
      h: button.offsetHeight + 8,
      x: orb.x + Math.cos(angle) * BLOOM_RADIUS,
      y: orb.y + Math.sin(angle) * BLOOM_RADIUS,
    };
  });
  relax(boxes, orb, rect);
  boxes.forEach((box) => {
    box.button.style.left = `${box.x}px`;
    box.button.style.top = `${box.y}px`;
  });
  requestAnimationFrame(() => layer.querySelectorAll('.mm-bloom-word').forEach((word) => word.classList.add('is-in')));
}

// A few rounds of pushing overlapping labels apart (axis-aligned boxes), away
// from the orb, and back inside the field.
function relax(boxes, orb, rect) {
  const orbReach = ORB_RADIUS + 18;
  for (let round = 0; round < 60; round += 1) {
    let moved = false;
    for (let a = 0; a < boxes.length; a += 1) {
      for (let b = a + 1; b < boxes.length; b += 1) {
        const first = boxes[a];
        const second = boxes[b];
        const overlapX = (first.w + second.w) / 2 - Math.abs(first.x - second.x);
        const overlapY = (first.h + second.h) / 2 - Math.abs(first.y - second.y);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        if (overlapX < overlapY) {
          const push = (overlapX / 2) * Math.sign(first.x - second.x || 1);
          first.x += push;
          second.x -= push;
        } else {
          const push = (overlapY / 2) * Math.sign(first.y - second.y || 1);
          first.y += push;
          second.y -= push;
        }
      }
    }
    for (const box of boxes) {
      const dx = box.x - orb.x;
      const dy = box.y - orb.y;
      const nearestX = Math.max(-box.w / 2, Math.min(box.w / 2, -dx));
      const nearestY = Math.max(-box.h / 2, Math.min(box.h / 2, -dy));
      const gap = Math.hypot(dx + nearestX, dy + nearestY);
      if (gap < orbReach) {
        const length = Math.hypot(dx, dy) || 1;
        box.x += (dx / length) * (orbReach - gap + 1);
        box.y += (dy / length) * (orbReach - gap + 1);
        moved = true;
      }
      box.x = Math.min(rect.width - box.w / 2, Math.max(box.w / 2, box.x));
      box.y = Math.min(rect.height - box.h / 2, Math.max(box.h / 2, box.y));
    }
    if (!moved) break;
  }
}

export function onClick(event, ctx) {
  const word = event.target.closest('.mm-bloom-word');
  if (!word) return false;
  ctx.toggle(Number(word.dataset.cardIndex), word.dataset.word);
  return true;
}

export function refresh(i, ctx) {
  const selected = ctx.state(i).selected;
  document.getElementById(`mm-field-${i}`)?.querySelectorAll('.mm-bloom-word').forEach((word) => {
    word.classList.toggle('is-selected', selected.has(word.dataset.word));
  });
}

