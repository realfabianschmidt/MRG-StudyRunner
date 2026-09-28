// Blob view: the four Mood Meter quadrants as living shapes, after How We
// Feel's shape language -- red jagged, yellow flowering, green soft, blue
// hanging. They breathe out of phase and melt slightly into each other at
// the middle (an SVG "goo" filter). A tap grows the blob into the fullscreen
// word space. Records the chosen words, like the classic view.
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { prefersReducedMotion, runAnimation } from '/static/scripts/cards/card-motion.js';
import { openWordSpace } from './word-space.js';
import { QUADRANT_SHAPES, blobPath } from './mood-core.js';
import { reflectSelection } from './view-classic.js';

export const records = 'words';

// Blob centers in the 400 x 400 view box, by quadrant.
const CENTERS = Object.freeze({
  red: [112, 112],
  yellow: [288, 112],
  blue: [112, 288],
  green: [288, 288],
});
const RADIUS = 80;
const PHASES = Object.freeze({ red: 0, yellow: 1.7, green: 3.1, blue: 4.6 });

export function render(_q, i, ctx) {
  const quads = ctx.quads(i);
  const paths = quads.map((quad) => {
    const [cx, cy] = CENTERS[quad.id];
    return `<path class="mm-blob" data-blob="${quad.id}" fill="${quad.color}"
      d="${blobPath(cx, cy, RADIUS, QUADRANT_SHAPES[quad.id], PHASES[quad.id])}"></path>`;
  }).join('');
  const labels = quads.map((quad) => {
    const [cx, cy] = CENTERS[quad.id];
    return `
      <button class="mm-blob-hit" type="button" data-card-index="${i}" data-quadrant="${quad.id}"
              style="left:${(cx / 4).toFixed(1)}%;top:${(cy / 4).toFixed(1)}%;">
        <span class="mm-quad-label">${escapeHtml(quad.label)}</span>
        <span class="mm-quad-examples">${quad.examples.slice(0, 3).map((word) => escapeHtml(word)).join(' - ')}</span>
      </button>`;
  }).join('');
  return `
    <div class="mm-blobs" id="mm-blobs-${i}">
      <svg class="mm-blobs-svg" viewBox="0 0 400 400" aria-hidden="true" focusable="false">
        <defs>
          <filter id="mm-goo-${i}" x="-10%" y="-10%" width="120%" height="120%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="9" result="blur"></feGaussianBlur>
            <feColorMatrix in="blur" mode="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 22 -9"></feColorMatrix>
          </filter>
        </defs>
        <g filter="url(#mm-goo-${i})">${paths}</g>
      </svg>
      ${labels}
    </div>`;
}

export function bind(cardElement, i) {
  const container = cardElement.querySelector(`#mm-blobs-${i}`);
  const svg = container?.querySelector('.mm-blobs-svg');
  if (!svg || prefersReducedMotion()) return;
  const paths = [...svg.querySelectorAll('.mm-blob')];
  runAnimation(svg, (time) => {
    if (container.offsetParent === null) return true; // card not shown: idle cheaply
    for (const path of paths) {
      const id = path.dataset.blob;
      const [cx, cy] = CENTERS[id];
      const hit = container.querySelector(`.mm-blob-hit[data-quadrant="${id}"]`);
      const emphasis = hit?.classList.contains('mm-blob-hit--chosen') ? 1.08
        : hit?.classList.contains('mm-blob-hit--dimmed') ? 0.9 : 1;
      const breathe = 1 + 0.035 * Math.sin(time * 0.9 + PHASES[id]);
      path.setAttribute('d', blobPath(cx, cy, RADIUS * emphasis * breathe, QUADRANT_SHAPES[id], time + PHASES[id]));
    }
    return true;
  });
}

export function onClick(event, ctx) {
  const hit = event.target.closest('.mm-blob-hit');
  if (!hit) return false;
  const i = Number(hit.dataset.cardIndex);
  openWordSpace({
    quads: ctx.quads(i),
    quadId: hit.dataset.quadrant,
    originRect: hit.getBoundingClientRect(),
    reveal: 'circle',
    isSelected: (word) => ctx.state(i).selected.has(word),
    selectedCount: () => ctx.state(i).selected.size,
    onToggle: (word) => ctx.toggle(i, word),
    onClose: () => refresh(i, ctx),
  });
  return true;
}

export function refresh(i, ctx) {
  const container = document.getElementById(`mm-blobs-${i}`);
  reflectSelection(container, i, ctx, {
    chosenClass: 'mm-blob-hit--chosen',
    dimmedClass: 'mm-blob-hit--dimmed',
  });
  // The shapes follow their labels: unchosen ones fade once anything is chosen.
  container?.querySelectorAll('.mm-blob').forEach((path) => {
    const hit = container.querySelector(`.mm-blob-hit[data-quadrant="${path.dataset.blob}"]`);
    path.classList.toggle('mm-blob--dimmed', Boolean(hit?.classList.contains('mm-blob-hit--dimmed')));
  });
}
