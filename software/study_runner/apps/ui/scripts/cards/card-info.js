// The shared frame every card editor is composed into.
//
// The sidebar always reads in the order an author writes a question:
//   question text -> instruction -> the card's own settings -> note -> toggles
//
// The first two, the note and the toggle group are the same for every card
// type, so they live here once and admin-controller's openOverlay composes
// them around cardModule.renderEditor(). Before this they were copied into
// nine card modules, which is how they drifted out of order.
//
// Two optional strings live on any question object:
//   info_top    - an instruction shown directly below the question/prompt
//   info_bottom - a plain note (no background) shown below the card body
//
// The Required toggle applies to every type that has an actual answer -
// participant-id (per-field required instead), stimulus and finish are not
// real questions and are excluded, mirroring NON_ANSWER_QUESTION_TYPES in
// validation.py.

import { t } from '../shared/i18n.js';
import { escapeHtml } from '../shared/dom-utils.js';

const NON_ANSWER_QUESTION_TYPES = new Set(['participant-id', 'stimulus', 'finish']);

// Cards that ask nothing, so there is no question text to write. Today this is
// the same three types as above, but for a different reason - a card could
// perfectly well have a prompt and no answer, so they stay separate.
const PROMPTLESS_QUESTION_TYPES = new Set(['participant-id', 'stimulus', 'finish']);

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

/**
 * The question text, always first.
 *
 * `placeholder` lets a card suggest what belongs there (the mood meter asks
 * "How do you feel right now?"); everything else falls back to the generic
 * prompt.
 */
export function renderPromptField(q, placeholder) {
  if (PROMPTLESS_QUESTION_TYPES.has(q?.type)) return '';
  const hint = placeholder?.key
    ? t(placeholder.key, placeholder.fallback ?? '')
    : t('editor.enterQuestion', 'Enter question...');
  return `
    <div class="field">
      <label>${escapeHtml(t('editor.questionText', 'Question text'))}</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q?.prompt ?? '')}" placeholder="${escapeHtml(hint)}">
    </div>`;
}

export function renderInstructionField(q) {
  if (PROMPTLESS_QUESTION_TYPES.has(q?.type)) return '';
  const instruction = q?.info_top ?? q?.subtitle ?? '';
  return `
    <div class="field">
      <label>${escapeHtml(t('cards.info.topLabel', 'Instruction below question'))}</label>
      <textarea class="fi-textarea ci-info-top" rows="2" placeholder="${escapeHtml(t('cards.info.topPlaceholder', 'Optional instruction shown below the question'))}">${escapeHtml(instruction)}</textarea>
    </div>`;
}

export function renderNoteField(q) {
  return `
    <div class="field">
      <label>${escapeHtml(t('cards.info.bottomLabel', 'Note (bottom)'))}</label>
      <textarea class="fi-textarea ci-info-bottom" rows="2" placeholder="${escapeHtml(t('cards.info.bottomPlaceholder', 'Optional plain note shown below this card'))}">${escapeHtml(q?.info_bottom ?? '')}</textarea>
    </div>`;
}

/**
 * All switches of a card, in one group at the very bottom.
 *
 * `extra` is whatever the card itself contributes (the word cloud's "allow
 * multiple", for example) so a card never renders a lone switch halfway up
 * its settings.
 */
export function renderEditorToggles(q, extra = '') {
  const required = NON_ANSWER_QUESTION_TYPES.has(q?.type) ? '' : renderEditorToggle({
    className: 'ci-required',
    checked: q?.required !== false,
    label: t('editor.requiredLabel', 'Required'),
    title: t('editor.requiredHint', 'Optional questions can be skipped and show an "optional" tag.'),
  });
  const toggles = `${required}${extra}`;
  return toggles ? `<div class="editor-toggles">${toggles}</div>` : '';
}

/**
 * One switch: label, no explanation, no panel behind it.
 *
 * The explanation is the `title`, so it is there when someone wants it and
 * does not turn a row of switches into a wall of small print.
 */
export function renderEditorToggle({ className, checked, label, title = '', disabled = false }) {
  return `
    <label class="editor-toggle${disabled ? ' editor-toggle--locked' : ''}"${title ? ` title="${escapeHtml(title)}"` : ''}>
      <span>${escapeHtml(label)}</span>
      <span class="switch"><input type="checkbox" class="${escapeHtml(className)}" ${checked ? 'checked' : ''}${disabled ? ' disabled' : ''}><span class="switch-slider"></span></span>
    </label>`;
}

export function renderOptionalTag(q) {
  if (q?.required !== false) return '';
  return `<div class="q-optional-tag">${escapeHtml(t('study.optionalTag', 'optional'))}</div>`;
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
