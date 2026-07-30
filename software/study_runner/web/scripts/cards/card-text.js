import { t } from '../i18n.js';
import { escapeHtml } from '../lib/dom-utils.js';
import { renderCardInstruction } from './card-info.js';

export const meta = { type:'text', icon:'chat-bubble', label:'Free text', pill:'pill-text' };

export const defaultQuestion = { type:'text', prompt:'' };

export function renderStudy(q, i) {
  return `
    <div class="q-type-tag"><i class="iconoir-chat-bubble"></i> ${escapeHtml(t('cards.text.tag', 'Free answer'))}</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    ${renderCardInstruction(q)}
    <textarea class="fi-textarea" id="q${i}" placeholder="Your answer..."></textarea>`;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>${escapeHtml(t('editor.questionText', 'Question text'))}</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="${escapeHtml(t('editor.enterQuestion', 'Enter question...'))}">
    </div>`;
}

export function collectConfig(el) {
  return {
    type: 'text',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
  };
}

export function collectAnswer(i) {
  return document.getElementById(`q${i}`)?.value || '';
}
