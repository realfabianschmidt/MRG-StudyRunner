import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { renderCardInstruction } from '/static/scripts/cards/card-info.js';

export { escapeHtml };

export const meta = {
  type: 'finish',
  icon: 'check-circle',
  label: 'End Screen',
  pill: 'pill-finish',
};


export function renderStudy(q, _i) {
  return `
    <div class="q-type-tag"><i class="iconoir-check-circle"></i> ${escapeHtml(t('cards.finish.tag', 'End'))}</div>
    <div class="done-icon" style="margin-top: 40px; margin-bottom: 24px;"><i class="iconoir-check"></i></div>
    <h1 class="screen-title" style="text-align: center;">${escapeHtml(q.title || defaultQuestion.title)}</h1>
    <p class="screen-sub" style="text-align: center; white-space: pre-wrap;">${escapeHtml(q.prompt || defaultQuestion.prompt)}</p>
    ${renderCardInstruction(q)}
  `;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>${escapeHtml(t('cards.finish.titleLabel', 'Headline'))}</label>
      <input type="text" class="fi-input q-title-input" value="${escapeHtml(q.title || defaultQuestion.title)}">
    </div>
    <div class="field">
      <label>${escapeHtml(t('cards.finish.messageLabel', 'Message'))}</label>
      <textarea class="fi-textarea q-prompt-input" rows="3">${escapeHtml(q.prompt || defaultQuestion.prompt)}</textarea>
    </div>
    <p class="editor-hint" style="margin-top:0.75rem;font-size:0.8rem;opacity:0.6;">
      ${escapeHtml(t('cards.finish.editorHint', 'This card is shown automatically at the end of the study after the data has been saved successfully.'))}
    </p>
  `;
}

export function collectConfig(el) {
  return {
    type: 'finish',
    title: el.querySelector('.q-title-input')?.value.trim() || '',
    prompt: el.querySelector('.q-prompt-input')?.value.trim() || '',
  };
}

export function collectAnswer() {
  return null;
}

export let defaultQuestion;
export function configureCard(defaults) { defaultQuestion = defaults['finish']; }
meta.hideSharedPrompt = true;
export const metaByType = { 'finish': meta };
