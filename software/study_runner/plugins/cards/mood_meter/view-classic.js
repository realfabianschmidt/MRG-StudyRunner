// Classic view: Brackett's Mood Meter as four colored tiles; a tile opens the
// fullscreen word space on its quadrant. Records the chosen words.
import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { openWordSpace } from './word-space.js';

export const records = 'words';

// Max number of selected words shown on an overview tile before "+N more".
const MAX_TILE_WORDS = 3;

export function render(_q, i, ctx) {
  // Tiles sit where their quadrant is on the Mood Meter: high energy on top,
  // pleasant on the right (blue bottom left, green bottom right).
  const byPlace = [...ctx.quads(i)].sort((a, b) => a.dirY - b.dirY || a.dirX - b.dirX);
  const tiles = byPlace.map((quad) => `
    <button class="mm-quad-btn" data-card-index="${i}" data-quadrant="${quad.id}"
            style="--mm-color:${quad.color};">
      <span class="mm-quad-label">${escapeHtml(quad.label)}</span>
      <span class="mm-quad-examples">${quad.examples.slice(0, 3).map((word) => escapeHtml(word)).join(' - ')}</span>
    </button>`).join('');
  return `<div class="mm-grid" id="mm-grid-${i}">${tiles}</div>`;
}

export function onClick(event, ctx) {
  const tile = event.target.closest('.mm-quad-btn');
  if (!tile) return false;
  const i = Number(tile.dataset.cardIndex);
  openWordSpace({
    quads: ctx.quads(i),
    quadId: tile.dataset.quadrant,
    originRect: tile.getBoundingClientRect(),
    reveal: 'zoom',
    isSelected: (word) => ctx.state(i).selected.has(word),
    selectedCount: () => ctx.state(i).selected.size,
    onToggle: (word) => ctx.toggle(i, word),
    onClose: () => refresh(i, ctx),
  });
  return true;
}

export function refresh(i, ctx) {
  reflectSelection(document.getElementById(`mm-grid-${i}`), i, ctx, {
    chosenClass: 'mm-quad-btn--chosen',
    dimmedClass: 'mm-quad-btn--dimmed',
  });
}

/**
 * Reflect the current selection on a quadrant overview (the classic tiles or
 * the blobs): quadrants with a selection stay in full color and list the
 * chosen words; the rest fade to their label once anything is selected.
 */
export function reflectSelection(root, i, ctx, { chosenClass, dimmedClass }) {
  if (!root) return;
  const selected = ctx.state(i).selected;
  const anySelected = selected.size > 0;

  ctx.quads(i).forEach((quad) => {
    const tile = root.querySelector(`[data-quadrant="${quad.id}"][data-card-index]`);
    if (!tile) return;
    const chosenWords = quad.words.filter((word) => selected.has(word));
    const chosen = chosenWords.length > 0;
    tile.classList.toggle(chosenClass, chosen);
    tile.classList.toggle(dimmedClass, anySelected && !chosen);

    const examples = tile.querySelector('.mm-quad-examples');
    if (!examples) return;
    if (chosen) {
      const shown = chosenWords.slice(0, MAX_TILE_WORDS).join(' · ');
      const extra = chosenWords.length - MAX_TILE_WORDS;
      examples.textContent = extra > 0
        ? `${shown}  ${t('cards.moodMeter.more', '+{n} more').replace('{n}', String(extra))}`
        : shown;
    } else if (anySelected) {
      examples.textContent = '';
    } else {
      examples.textContent = quad.examples.slice(0, 3).join(' - ');
    }
  });
}
