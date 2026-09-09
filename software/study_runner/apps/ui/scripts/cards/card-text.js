import { renderStudyHeader } from './card-info.js';

export const meta = { type:'text', icon:'chat-bubble', label:'Free text', pill:'pill-text' };

export const defaultQuestion = { type:'text', prompt:'' };

export function renderStudy(q, i) {
  return `
    ${renderStudyHeader(q, { icon: 'chat-bubble', tagKey: 'cards.text.tag', tagFallback: 'Free answer' })}
    <textarea class="fi-textarea" id="q${i}" placeholder="Your answer..."></textarea>`;
}

export function renderEditor(q) {
  return ``;
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

export function isAnswered(_question, questionIndex) {
  return (collectAnswer(questionIndex) || '').trim().length > 0;
}
