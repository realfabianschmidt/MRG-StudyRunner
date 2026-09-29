// Mood Meter card: one card, four views, chosen in the card settings.
//   classic  Brackett's Mood Meter tiles + word space          -> words
//   blobs    living quadrant shapes + word space               -> words
//   field    Affect Grid with a shape-shifting orb             -> words + position
//   orbit    circumplex / Geneva Emotion Wheel with a fisheye  -> words + position
// Each view lives in its own module (view-*.js) behind the same small
// interface; this file is the card contract and the editor. Per-session state
// lives only in cardState() (cards/session-state.js). See README.md.
import { t } from '/static/scripts/shared/i18n.js';
import { notifyCardChanged, renderEditorToggle, renderStudyHeader } from '/static/scripts/cards/card-info.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { cardState, onSessionReset } from '/static/scripts/cards/session-state.js';
import { openHelpModal } from '/static/scripts/shared/plugin-help.js';
import { QUADRANTS, QUADRANT_SHAPES, blobPath, colorAt, shapeAt, wordLists } from './mood-core.js';
import { closeWordSpace } from './word-space.js';
import * as classic from './view-classic.js';
import * as blobs from './view-blobs.js';
import * as field from './view-field.js';
import * as orbit from './view-orbit.js';


export const meta = { type: 'mood-meter', icon: 'app-window', label: 'Mood Meter', pill: 'pill-mood-meter' };

const VIEWS = Object.freeze({ classic, blobs, field, orbit });
const VARIANTS = Object.freeze(['classic', 'blobs', 'field', 'orbit']);

// Per-session state for one card: the chosen words, where the participant
// placed themselves (field/orbit), and the question config. Lives in the
// shared session store so the next participant starts empty.
function getState(i) {
  return cardState('mood-meter', i, () => ({ selected: new Set(), position: null, question: null }));
}

function questionFor(i) {
  return getState(i).question ?? defaultQuestion;
}

function variantOf(q) {
  return VIEWS[q?.variant] ? q.variant : 'classic';
}

function viewFor(i) {
  return VIEWS[variantOf(questionFor(i))];
}

// What the views may use; see view-classic.js for the smallest example.
const CTX = Object.freeze({
  state: getState,
  question: questionFor,
  quads: (i) => wordLists(questionFor(i), defaultQuestion, t),
  toggle: toggleWord,
  setPosition,
});

// Animations stop by themselves on every reset (cards/card-motion.js).
onSessionReset(() => closeWordSpace(false));

function toggleWord(i, word) {
  const state = getState(i);
  if (state.selected.has(word)) {
    state.selected.delete(word);
  } else {
    if (questionFor(i)?.allow_multiple === false) state.selected.clear();
    state.selected.add(word);
  }
  refreshCard(i);
  notifyCardChanged(document.getElementById(`mm-card-${i}`));
}

function setPosition(i, { pleasantness, energy, intensity }) {
  const round = (value) => Math.round(Math.min(1, Math.max(0, value)) * 1000) / 1000;
  const position = { pleasantness: round(pleasantness), energy: round(energy) };
  if (intensity !== undefined) position.intensity = round(intensity);
  getState(i).position = position;
  notifyCardChanged(document.getElementById(`mm-card-${i}`), 'position');
}

function refreshCard(i) {
  viewFor(i).refresh?.(i, CTX);
  renderChips(i);
}

// The chosen words under the field and the orbit; a tap removes one.
function renderChips(i) {
  const chips = document.getElementById(`mm-chips-${i}`);
  if (!chips) return;
  const words = [...getState(i).selected];
  const removeLabel = t('cards.moodMeter.removeWord', 'Remove {word}');
  chips.innerHTML = words.map((word) => `
    <button type="button" class="mm-chip" data-card-index="${i}" data-word="${escapeHtml(word)}"
            aria-label="${escapeHtml(removeLabel.replace('{word}', word))}">
      ${escapeHtml(word)} <i class="iconoir-xmark" aria-hidden="true"></i>
    </button>`).join('');
}

export function renderStudy(q, i) {
  getState(i).question = q;
  const variant = variantOf(q);
  const view = VIEWS[variant];
  const chips = view.records === 'words+position'
    ? `<div class="mm-chips" id="mm-chips-${i}" aria-live="polite"></div>`
    : '';
  return `
    ${renderStudyHeader(q, { icon: 'app-window', tagKey: 'cards.moodMeter.tag', tagFallback: 'Mood' })}
    <div class="mm-card mm-card--${variant}" id="mm-card-${i}" data-card-index="${i}">
      ${view.render(q, i, CTX)}
      ${chips}
    </div>`;
}

export function bindInteractions(cardElement, i) {
  viewFor(i).bind?.(cardElement, i, CTX);
}

// Called by study-controller's delegated click handler on #q-container
export function onClick(event) {
  const chip = event.target.closest('.mm-chip');
  if (chip) {
    toggleWord(Number(chip.dataset.cardIndex), chip.dataset.word);
    return true;
  }
  const card = event.target.closest('.mm-card');
  if (!card) return false;
  return viewFor(Number(card.dataset.cardIndex)).onClick?.(event, CTX) === true;
}

export function collectAnswer(i) {
  const state = getState(i);
  const words = [...state.selected];
  if (!words.length) return null;
  if (viewFor(i).records !== 'words+position') return words;
  if (!state.position) return null;
  return { words, ...state.position };
}

export function isAnswered(_question, questionIndex) {
  return collectAnswer(questionIndex) !== null;
}

// ── Editor ───────────────────────────────────────────────────────────────────

// The generic prompt field asks a generic question; this card suggests its own.
export const promptPlaceholder = { key: 'editor.moodMeterPlaceholder', fallback: 'How do you feel right now?' };

const VARIANT_TEXT = Object.freeze({
  classic: ['Classic', 'Four colored tiles, then the word space.'],
  blobs: ['Blobs', 'Four breathing shapes that grow into the word space.'],
  field: ['Field', 'Drag a shape-shifting light across the mood field.'],
  orbit: ['Orbit', 'A feelings wheel: touch where you are, tap your words.'],
});

function variantName(variant) {
  return t(`cards.moodMeter.variant.${variant}`, VARIANT_TEXT[variant][0]);
}

function recordsText(variant) {
  return VIEWS[variant].records === 'words+position'
    ? t('cards.moodMeter.recordsPosition', 'Records the chosen words and the position (pleasantness, energy{extra}, each 0-1).')
      .replace('{extra}', variant === 'orbit' ? t('cards.moodMeter.recordsIntensity', ', intensity') : '')
    : t('cards.moodMeter.recordsWords', 'Records the chosen words.');
}

// Small static pictures of the four views for the editor tiles.
function variantPreview(variant) {
  const fill = (id) => QUADRANTS.find((quadrant) => quadrant.id === id).color;
  if (variant === 'classic') {
    return ['red', 'yellow', 'blue', 'green'].map((id, index) => (
      `<rect x="${10 + (index % 2) * 52}" y="${6 + Math.floor(index / 2) * 36}" width="48" height="32" rx="6" fill="${fill(id)}"/>`
    )).join('');
  }
  if (variant === 'blobs') {
    const centers = { red: [36, 24], yellow: [84, 24], blue: [36, 58], green: [84, 58] };
    return Object.entries(centers).map(([id, [cx, cy]]) => (
      `<path d="${blobPath(cx, cy, 15, QUADRANT_SHAPES[id], 0.6, 72)}" fill="${fill(id)}"/>`
    )).join('');
  }
  if (variant === 'field') {
    return `
      <defs><linearGradient id="mm-pv-top" x1="0" x2="1"><stop offset="0" stop-color="${fill('red')}"/><stop offset="1" stop-color="${fill('yellow')}"/></linearGradient>
      <linearGradient id="mm-pv-bottom" x1="0" x2="1"><stop offset="0" stop-color="${fill('blue')}"/><stop offset="1" stop-color="${fill('green')}"/></linearGradient></defs>
      <rect x="6" y="4" width="108" height="36" rx="8" fill="url(#mm-pv-top)" opacity=".75"/>
      <rect x="6" y="40" width="108" height="36" rx="8" fill="url(#mm-pv-bottom)" opacity=".75"/>
      <path d="${blobPath(42, 26, 13, shapeAt(0.2, 0.85), 0, 72)}" fill="${colorAt(0.2, 0.85)}" stroke="#fff" stroke-width="1.5"/>`;
  }
  const arcs = [['yellow', -90, 0], ['green', 0, 90], ['blue', 90, 180], ['red', 180, 270]].map(([id, from, to]) => {
    const point = (angle, radius) => [60 + Math.cos((angle * Math.PI) / 180) * radius, 40 + Math.sin((angle * Math.PI) / 180) * radius];
    const [x1, y1] = point(from, 34);
    const [x2, y2] = point(to, 34);
    return `<path d="M${x1.toFixed(1)} ${y1.toFixed(1)} A34 34 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}" stroke="${fill(id)}" stroke-width="7" fill="none"/>`;
  }).join('');
  return `${arcs}<circle cx="60" cy="40" r="20" fill="none" stroke="currentColor" stroke-opacity=".2"/><circle cx="74" cy="30" r="4" fill="${fill('yellow')}"/><circle cx="48" cy="52" r="3" fill="${fill('blue')}"/>`;
}

export function renderEditor(q) {
  const current = variantOf(q);
  const group = `mm-variant-${Math.random().toString(36).slice(2, 9)}`;
  const tiles = VARIANTS.map((variant) => `
    <label class="mm-ed-variant">
      <input type="radio" class="mm-ed-variant-input" name="${group}" value="${variant}" ${variant === current ? 'checked' : ''}>
      <svg class="mm-ed-variant-preview" viewBox="0 0 120 80" aria-hidden="true" focusable="false">${variantPreview(variant)}</svg>
      <span class="mm-ed-variant-name">${escapeHtml(variantName(variant))}</span>
      <span class="mm-ed-variant-desc">${escapeHtml(t(`cards.moodMeter.variant.${variant}Hint`, VARIANT_TEXT[variant][1]))}</span>
    </label>`).join('');

  const quadSections = wordLists(q, defaultQuestion, t).map((quad) => `
    <div class="field mm-ed-quad-field">
      <label class="mm-ed-quad-label" style="color:${quad.colorDark};">
        <span style="width:10px;height:10px;border-radius:50%;background:${quad.color};flex-shrink:0;"></span>
        ${escapeHtml(quad.label)}
      </label>
      <textarea class="mm-ed-words fi-textarea" data-quadrant="${quad.id}"
                style="min-height:140px;font-size:.75rem;background:var(--bg-elevated);"
                placeholder="${escapeHtml(t('editor.oneWordPerLine', 'One word per line'))}">${escapeHtml(quad.words.join('\n'))}</textarea>
    </div>`).join('');

  const helpLabel = t('cards.moodMeter.help.button', 'What are these views based on?');
  return `
    <div class="field mm-ed-variant-field">
      <div class="mm-ed-variant-head">
        <label>${escapeHtml(t('cards.moodMeter.variantLabel', 'View'))}</label>
        <button type="button" class="btn-icon-only plugin-help-button mm-ed-help"
                title="${escapeHtml(helpLabel)}" aria-label="${escapeHtml(helpLabel)}"><i class="iconoir-help-circle"></i></button>
      </div>
      <div class="mm-ed-variants" role="radiogroup">${tiles}</div>
      <p class="settings-hint mm-ed-records">${escapeHtml(recordsText(current))}</p>
    </div>
    <div class="mm-ed-quads">${quadSections}</div>`;
}

export function bindEditorEvents(el) {
  el.querySelector('.mm-ed-help')?.addEventListener('click', () => openHelpModal(helpContent()));
  el.querySelectorAll('.mm-ed-variant-input').forEach((input) => {
    input.addEventListener('change', () => {
      const records = el.querySelector('.mm-ed-records');
      if (records && input.checked) records.textContent = recordsText(input.value);
    });
  });
}

// Contributed to the shared toggle group at the bottom, so this card does not
// leave a lone switch sitting between its word lists.
export function renderEditorToggles(q) {
  return renderEditorToggle({
    className: 'qe-allow-multiple',
    checked: q?.allow_multiple !== false,
    label: t('editor.allowMultipleWords', 'Allow selecting multiple words'),
    title: t('editor.allowMultipleWordsHint', 'Off means the participant picks exactly one word.'),
  });
}

export function collectConfig(el) {
  const word_lists = {};
  el.querySelectorAll('.mm-ed-words').forEach(ta => {
    const quadId = ta.dataset.quadrant;
    const words = ta.value.split('\n').map(l => l.trim()).filter(Boolean);
    const defaults = defaultQuestion?.word_lists?.[quadId];
    if (JSON.stringify(words) !== JSON.stringify(defaults)) {
      word_lists[quadId] = words;
    }
  });
  const hasCustomWords = Object.keys(word_lists).length > 0;
  const variant = el.querySelector('.mm-ed-variant-input:checked')?.value;
  return {
    type: 'mood-meter',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    variant: VIEWS[variant] ? variant : 'classic',
    allow_multiple: el.querySelector('.qe-allow-multiple')?.checked !== false,
    word_lists: hasCustomWords ? word_lists : null,
  };
}

// ── Help: what each view is based on, with sources ───────────────────────────

const SOURCES = Object.freeze({
  ruler: {
    label: 'Brackett, M. A., Bailey, C. S., Hoffmann, J. D., & Simmons, D. N. (2019). RULER: A theory-driven, systemic approach to social, emotional, and academic learning. Educational Psychologist, 54(3), 144-161.',
    url: 'https://doi.org/10.1080/00461520.2019.1614447',
  },
  moodMeter: { label: 'Yale Center for Emotional Intelligence: RULER and the Mood Meter', url: 'https://www.rulerapproach.org/' },
  howWeFeel: { label: 'How We Feel (Brackett et al.): shape language of the Mood Meter', url: 'https://howwefeel.substack.com/p/we-feelgrateful' },
  affectGrid: {
    label: 'Russell, J. A., Weiss, A., & Mendelsohn, G. A. (1989). Affect Grid: A single-item scale of pleasure and arousal. Journal of Personality and Social Psychology, 57(3), 493-502.',
    url: 'https://doi.org/10.1037/0022-3514.57.3.493',
  },
  stateOfMind: { label: 'Apple: Log your state of mind (valence shape)', url: 'https://support.apple.com/guide/iphone/log-your-state-of-mind-iph6a6decb13/ios' },
  circumplex: {
    label: 'Russell, J. A. (1980). A circumplex model of affect. Journal of Personality and Social Psychology, 39(6), 1161-1178.',
    url: 'https://doi.org/10.1037/h0077714',
  },
  gew: {
    label: 'Scherer, K. R. (2005). What are emotions? And how can they be measured? Social Science Information, 44(4), 695-729.',
    url: 'https://doi.org/10.1177/0539018405058216',
  },
  gewSite: { label: 'Swiss Center for Affective Sciences: The Geneva Emotion Wheel', url: 'https://www.unige.ch/cisa/gew' },
});

function helpContent() {
  return {
    title: t('cards.moodMeter.help.title', 'Mood Meter views'),
    intro: t('cards.moodMeter.help.intro', 'All four views use the same word lists and the same two dimensions: **energy** (bottom to top) and **pleasantness** (left to right). The default words are Brackett\'s original 10 x 10 Mood Meter grid, read row by row, so every word sits where the Mood Meter puts it.'),
    sections: [
      {
        heading: variantName('classic'),
        text: t('cards.moodMeter.help.classic', 'The Mood Meter of the RULER approach: four colored quadrants, then all words on a plane the participant can pan. Records the chosen words.'),
        references: [SOURCES.ruler, SOURCES.moodMeter],
      },
      {
        heading: variantName('blobs'),
        text: t('cards.moodMeter.help.blobs', 'The same four quadrants as breathing shapes, after the shape language of the How We Feel app: jagged for high-energy unpleasant, flowering for high-energy pleasant, soft for calm, hanging for low. A tap grows the shape into the word space. Records the chosen words.'),
        references: [SOURCES.ruler, SOURCES.howWeFeel],
      },
      {
        heading: variantName('field'),
        text: t('cards.moodMeter.help.field', 'An Affect Grid: the participant places one point on pleasantness x energy by dragging a light whose shape follows the feeling (as in Apple\'s State of Mind). The nearest words appear around it. Records the chosen words and the point.'),
        references: [SOURCES.affectGrid, SOURCES.stateOfMind],
      },
      {
        heading: variantName('orbit'),
        text: t('cards.moodMeter.help.orbit', 'The circumplex of affect as a wheel, read like the Geneva Emotion Wheel: the angle is the kind of feeling, the distance from the middle its intensity. Words near the finger grow. Records the chosen words, the point and the intensity.'),
        references: [SOURCES.circumplex, SOURCES.gew, SOURCES.gewSite],
      },
      {
        heading: t('cards.moodMeter.help.dataTitle', 'The recorded position'),
        text: t('cards.moodMeter.help.data', 'Field and orbit add `pleasantness` and `energy` from 0 to 1 (0.5 is neutral) to the answer, orbit also `intensity` (0 = middle, 1 = rim). To compare with the 9-point Affect Grid use `1 + 8 * value`.'),
        references: [SOURCES.affectGrid],
      },
    ],
  };
}

export let defaultQuestion;
export function configureCard(defaults) { defaultQuestion = defaults['mood-meter']; }
export const metaByType = { 'mood-meter': meta };
