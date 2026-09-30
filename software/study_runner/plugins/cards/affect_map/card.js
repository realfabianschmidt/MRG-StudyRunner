// Affect Map: the positional Mood Meter views with per-card presentation colors.
import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { notifyCardChanged, renderEditorToggle, renderStudyHeader } from '/static/scripts/cards/card-info.js';
import { cardState } from '/static/scripts/cards/session-state.js';
import { QUADRANTS, colorAt, wordLists } from './mood-core.js';
import * as field from './view-field.js';
import * as orbit from './view-orbit.js';

export const meta = { type: 'affect-map', icon: 'app-window', label: 'Affect Map', pill: 'pill-mood-meter' };
export const metaByType = { 'affect-map': meta };
export const promptPlaceholder = { key: 'editor.moodMeterPlaceholder', fallback: 'How do you feel right now?' };
const VIEWS = Object.freeze({ field, orbit });
const DEFAULT_COLORS = Object.freeze(Object.fromEntries(QUADRANTS.map(({ id, color }) => [id, color])));
const NEUTRAL_COLORS = Object.freeze({ red: '#777777', yellow: '#AAAAAA', green: '#888888', blue: '#555555' });
const VALID_COLOR = /^#[0-9a-fA-F]{6}$/;

function state(i) {
  return cardState('affect-map', i, () => ({ selected: new Set(), position: null, question: null }));
}

function question(i) { return state(i).question ?? defaultQuestion; }
function variant(q) { return q?.variant === 'orbit' ? 'orbit' : 'field'; }
function view(i) { return VIEWS[variant(question(i))]; }

export function effectiveColors(q) {
  if (q?.colors_enabled === false) return NEUTRAL_COLORS;
  return Object.fromEntries(Object.entries(DEFAULT_COLORS).map(([id, fallback]) => {
    const value = q?.region_colors?.[id];
    return [id, typeof value === 'string' && VALID_COLOR.test(value) ? value : fallback];
  }));
}

const CTX = Object.freeze({
  state,
  question,
  quads: (i) => wordLists(question(i), defaultQuestion, t).map((quad) => ({
    ...quad, color: effectiveColors(question(i))[quad.id],
  })),
  colorAt: (i, pleasantness, energy) => colorAt(pleasantness, energy, effectiveColors(question(i))),
  toggle: toggleWord,
  setPosition,
});

function toggleWord(i, word) {
  const current = state(i);
  if (current.selected.has(word)) current.selected.delete(word);
  else {
    if (question(i).allow_multiple === false) current.selected.clear();
    current.selected.add(word);
  }
  view(i).refresh?.(i, CTX);
  renderChips(i);
  notifyCardChanged(document.getElementById(`mm-card-${i}`));
}

function setPosition(i, { pleasantness, energy, intensity }) {
  const round = (value) => Math.round(Math.min(1, Math.max(0, value)) * 1000) / 1000;
  state(i).position = { pleasantness: round(pleasantness), energy: round(energy) };
  if (intensity !== undefined) state(i).position.intensity = round(intensity);
  notifyCardChanged(document.getElementById(`mm-card-${i}`), 'position');
}

function renderChips(i) {
  const chips = document.getElementById(`mm-chips-${i}`);
  if (!chips) return;
  const label = t('cards.moodMeter.removeWord', 'Remove {word}');
  chips.innerHTML = [...state(i).selected].map((word) => `
    <button type="button" class="mm-chip" data-card-index="${i}" data-word="${escapeHtml(word)}"
            aria-label="${escapeHtml(label.replace('{word}', word))}">${escapeHtml(word)}
      <i class="iconoir-xmark" aria-hidden="true"></i></button>`).join('');
}

export function renderStudy(q, i) {
  state(i).question = q;
  const chosen = variant(q);
  return `${renderStudyHeader(q, { icon: 'app-window', tagKey: 'cardType.affectMap', tagFallback: 'Affect Map' })}
    <div class="mm-card mm-card--${chosen}" id="mm-card-${i}" data-card-index="${i}">
      ${VIEWS[chosen].render(q, i, CTX)}
      <div class="mm-chips" id="mm-chips-${i}" aria-live="polite"></div>
    </div>`;
}

export function bindInteractions(cardElement, i) { view(i).bind?.(cardElement, i, CTX); }

export function onClick(event) {
  const chip = event.target.closest('.mm-chip');
  if (chip) { toggleWord(Number(chip.dataset.cardIndex), chip.dataset.word); return true; }
  const card = event.target.closest('.mm-card');
  return card ? view(Number(card.dataset.cardIndex)).onClick?.(event, CTX) === true : false;
}

export function collectAnswer(i) {
  const current = state(i);
  if (!current.selected.size || !current.position) return null;
  return { words: [...current.selected], ...current.position };
}

export function isAnswered(_question, i) { return collectAnswer(i) !== null; }

function preview(variantName, colors) {
  const at = (p, e) => colorAt(p, e, colors);
  if (variantName === 'field') return `<defs><linearGradient id="am-preview-top"><stop stop-color="${colors.red}"/><stop offset="1" stop-color="${colors.yellow}"/></linearGradient><linearGradient id="am-preview-bottom"><stop stop-color="${colors.blue}"/><stop offset="1" stop-color="${colors.green}"/></linearGradient></defs><rect x="4" y="4" width="112" height="36" rx="6" fill="url(#am-preview-top)"/><rect x="4" y="40" width="112" height="36" rx="6" fill="url(#am-preview-bottom)"/><circle cx="68" cy="30" r="10" fill="${at(.6,.7)}" stroke="white" stroke-width="2"/>`;
  return `<circle cx="60" cy="40" r="32" fill="none" stroke="${colors.red}" stroke-width="9" stroke-dasharray="48 153" transform="rotate(180 60 40)"/><circle cx="60" cy="40" r="32" fill="none" stroke="${colors.yellow}" stroke-width="9" stroke-dasharray="48 153" transform="rotate(270 60 40)"/><circle cx="60" cy="40" r="32" fill="none" stroke="${colors.green}" stroke-width="9" stroke-dasharray="48 153"/><circle cx="60" cy="40" r="32" fill="none" stroke="${colors.blue}" stroke-width="9" stroke-dasharray="48 153" transform="rotate(90 60 40)"/><circle cx="60" cy="40" r="18" fill="none" stroke="currentColor" stroke-opacity=".3"/><circle cx="72" cy="30" r="4" fill="${at(.8,.8)}"/>`;
}

export function renderEditor(q) {
  const selected = variant(q);
  const colors = effectiveColors(q);
  const group = `am-variant-${Math.random().toString(36).slice(2, 9)}`;
  const views = ['field', 'orbit'].map((name) => `<label class="mm-ed-variant">
    <input type="radio" class="am-ed-variant" name="${group}" value="${name}" ${name === selected ? 'checked' : ''}>
    <svg class="mm-ed-variant-preview" viewBox="0 0 120 80" aria-hidden="true">${preview(name, colors)}</svg>
    <span class="mm-ed-variant-name">${escapeHtml(t(`cards.moodMeter.variant.${name}`, name))}</span>
    <span class="mm-ed-variant-desc">${escapeHtml(t(`cards.moodMeter.variant.${name}Hint`, name))}</span>
  </label>`).join('');
  const enabled = q?.colors_enabled !== false;
  const colorInputs = QUADRANTS.map((quad) => `<label class="field am-ed-color">
    <span>${escapeHtml(t(`cards.moodMeter.quadrant.${quad.id}`, quad.label))}</span>
    <input type="color" class="am-ed-color-input" data-region="${quad.id}" value="${escapeHtml(q?.region_colors?.[quad.id] || DEFAULT_COLORS[quad.id])}">
  </label>`).join('');
  const wordInputs = wordLists(q, defaultQuestion, t).map((quad) => `<div class="field mm-ed-quad-field">
    <label class="mm-ed-quad-label">${escapeHtml(quad.label)}</label>
    <textarea class="mm-ed-words fi-textarea" data-quadrant="${quad.id}" placeholder="${escapeHtml(t('editor.oneWordPerLine', 'One word per line'))}">${escapeHtml(quad.words.join('\n'))}</textarea>
  </div>`).join('');
  return `<div class="field mm-ed-variant-field">
    <label>${escapeHtml(t('cards.moodMeter.variantLabel', 'View'))}</label>
    <div class="mm-ed-variants" role="radiogroup">${views}</div>
    <p class="settings-hint">${escapeHtml(t('cards.affectMap.answerHint', 'Records chosen words and position; Orbit also records intensity.'))}</p>
  </div>
  <div class="field"><label class="am-ed-enable-label"><input type="checkbox" class="am-ed-colors-enabled" ${enabled ? 'checked' : ''}> ${escapeHtml(t('cards.affectMap.showColors', 'Show colors'))}</label>
    <p class="settings-hint">${escapeHtml(t('cards.affectMap.colorHint', 'When off, all regions, words and the light are neutral gray.'))}</p>
  </div>
  <div class="am-ed-colors">${colorInputs}</div>
  <div class="mm-ed-quads">${wordInputs}</div>`;
}

export function bindEditorEvents(el) {
  const update = () => {
    const colors = effectiveColors({
      colors_enabled: el.querySelector('.am-ed-colors-enabled')?.checked,
      region_colors: Object.fromEntries([...el.querySelectorAll('.am-ed-color-input')].map((input) => [input.dataset.region, input.value])),
    });
    el.querySelectorAll('.am-ed-variant').forEach((input) => {
      const svg = input.closest('label')?.querySelector('svg');
      if (svg) svg.innerHTML = preview(input.value, colors);
    });
  };
  el.querySelector('.am-ed-colors-enabled')?.addEventListener('change', update);
  el.querySelectorAll('.am-ed-color-input').forEach((input) => input.addEventListener('input', update));
}

export function renderEditorToggles(q) {
  return renderEditorToggle({
    className: 'qe-allow-multiple', checked: q?.allow_multiple !== false,
    label: t('editor.allowMultipleWords', 'Allow selecting multiple words'),
    title: t('editor.allowMultipleWordsHint', 'Off means the participant picks exactly one word.'),
  });
}

export function collectConfig(el) {
  const word_lists = {};
  el.querySelectorAll('.mm-ed-words').forEach((input) => {
    const words = input.value.split('\n').map((line) => line.trim()).filter(Boolean);
    if (JSON.stringify(words) !== JSON.stringify(defaultQuestion.word_lists[input.dataset.quadrant])) word_lists[input.dataset.quadrant] = words;
  });
  return {
    type: 'affect-map', prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    variant: el.querySelector('.am-ed-variant:checked')?.value || 'field',
    allow_multiple: el.querySelector('.qe-allow-multiple')?.checked !== false,
    word_lists: Object.keys(word_lists).length ? word_lists : null,
    colors_enabled: el.querySelector('.am-ed-colors-enabled')?.checked !== false,
    region_colors: Object.fromEntries([...el.querySelectorAll('.am-ed-color-input')].map((input) => [input.dataset.region, input.value])),
  };
}

export let defaultQuestion;
export function configureCard(defaults) { defaultQuestion = defaults['affect-map']; }
