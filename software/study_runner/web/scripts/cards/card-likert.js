import { t } from '../i18n.js';
import { escapeHtml } from '../lib/dom-utils.js';
import { renderCardInstruction } from './card-info.js';

export const meta = { type:'likert', icon:'list-select', label:'Likert scale', pill:'pill-likert' };

export const defaultQuestion = {
  type:'likert', prompt:'', scale:7, label_min:'not at all', label_max:'very strongly',
};

export function renderStudy(q, i) {
  const scale = q.scale || 7;
  let opts = '';
  for (let v = 1; v <= scale; v++) {
    opts += `<input type="radio" name="q${i}" value="${v}" id="q${i}v${v}"><label for="q${i}v${v}">${v}</label>`;
  }
  return `
    <div class="q-type-tag"><i class="iconoir-list-select"></i> ${escapeHtml(t('cards.likert.tag', 'Rating scale'))}</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    ${renderCardInstruction(q)}
    <div class="likert-scale-row">
      <span class="likert-pole">${escapeHtml(q.label_min||'')}</span>
      <div class="likert-row">${opts}</div>
      <span class="likert-pole likert-pole--right">${escapeHtml(q.label_max||'')}</span>
    </div>`;
}

export function renderEditor(q) {
  return `
    <div class="row3">
      <div class="field">
        <label>${escapeHtml(t('editor.scalePoints', 'Scale points'))}</label>
        <input type="number" class="qe-scale" value="${q.scale||7}" min="3" max="11">
      </div>
      <div class="field">
        <label>${escapeHtml(t('editor.leftLabel', 'Left label'))}</label>
        <input type="text" class="qe-lmin" value="${escapeHtml(q.label_min||'')}">
      </div>
      <div class="field">
        <label>${escapeHtml(t('editor.rightLabel', 'Right label'))}</label>
        <input type="text" class="qe-lmax" value="${escapeHtml(q.label_max||'')}">
      </div>
    </div>`;
}

export function collectConfig(el) {
  return {
    type: 'likert',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    scale: Number.parseInt(el.querySelector('.qe-scale')?.value, 10) || 7,
    label_min: el.querySelector('.qe-lmin')?.value.trim() || '',
    label_max: el.querySelector('.qe-lmax')?.value.trim() || '',
  };
}

export function collectAnswer(i) {
  const sel = document.querySelector(`input[name="q${i}"]:checked`);
  return sel ? Number.parseInt(sel.value, 10) : null;
}
