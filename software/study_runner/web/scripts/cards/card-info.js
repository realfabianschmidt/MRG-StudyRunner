// Shared instruction/note text and the Required toggle for every card.
//
// Two optional strings live on any question object:
//   info_top    - an instruction shown directly below the question/prompt
//   info_bottom - a plain note (no background) shown below the card body
//
// These render the same way for every card type, so the mechanism lives here
// once instead of being copied into each card module. The Required toggle
// lives here too, for every type that has an actual answer - participant-id
// (per-field required instead), stimulus and finish are not real questions
// and are excluded, mirroring NON_ANSWER_QUESTION_TYPES in validation.py.

import { t } from '../i18n.js';
import { escapeHtml } from '../lib/dom-utils.js';

const NON_ANSWER_QUESTION_TYPES = new Set(['participant-id', 'stimulus', 'finish']);

export function renderCardInstruction(q) {
  const text = String(q?.info_top ?? '').trim();
  if (!text) return '';
  return `<p class="card-instruction">${escapeHtml(text)}</p>`;
}

export const renderInfoTop = renderCardInstruction;

export function renderInfoBottom(q) {
  const text = String(q?.info_bottom ?? '').trim();
  if (!text) return '';
  return `<div class="card-info-bottom">${escapeHtml(text)}</div>`;
}

export function renderInfoEditor(q) {
  const instruction = q?.info_top ?? q?.subtitle ?? '';
  const requiredToggle = NON_ANSWER_QUESTION_TYPES.has(q?.type) ? '' : renderRequiredToggle(q);
  return `
    ${requiredToggle}
    <div class="field">
      <label>${escapeHtml(t('cards.info.topLabel', 'Instruction below question'))}</label>
      <textarea class="fi-textarea ci-info-top" rows="2" placeholder="${escapeHtml(t('cards.info.topPlaceholder', 'Optional instruction shown below the question'))}">${escapeHtml(instruction)}</textarea>
    </div>
    <div class="field">
      <label>${escapeHtml(t('cards.info.bottomLabel', 'Note (bottom)'))}</label>
      <textarea class="fi-textarea ci-info-bottom" rows="2" placeholder="${escapeHtml(t('cards.info.bottomPlaceholder', 'Optional plain note shown below this card'))}">${escapeHtml(q?.info_bottom ?? '')}</textarea>
    </div>`;
}

export function renderOptionalTag(q) {
  if (q?.required !== false) return '';
  return `<div class="q-optional-tag">${escapeHtml(t('study.optionalTag', 'optional'))}</div>`;
}

function renderRequiredToggle(q) {
  const required = q?.required !== false;
  return `
    <div class="field">
      <label class="switch-row" style="margin-bottom: 0;">
        <span style="display:flex; flex-direction:column; gap:2px;">
          <strong style="color:var(--ink);">${escapeHtml(t('editor.requiredLabel', 'Required'))}</strong>
          <small style="color:var(--ink-40); line-height:1.4;">${escapeHtml(t('editor.requiredHint', 'Optional questions can be skipped and show an "optional" tag.'))}</small>
        </span>
        <span class="switch"><input type="checkbox" class="ci-required" ${required ? 'checked' : ''}><span class="switch-slider"></span></span>
      </label>
    </div>`;
}

export function collectInfo(el) {
  const result = {};
  const top = el?.querySelector('.ci-info-top')?.value.trim() || '';
  const bottom = el?.querySelector('.ci-info-bottom')?.value.trim() || '';
  if (top) result.info_top = top;
  if (bottom) result.info_bottom = bottom;
  const requiredInput = el?.querySelector('.ci-required');
  if (requiredInput) result.required = requiredInput.checked;
  return result;
}
