import { t } from '../i18n.js';
import { escapeHtml } from '../lib/dom-utils.js';
import { renderCardInstruction } from './card-info.js';

export const meta = { type:'slider', icon:'sliders-vertical', label:'Slider (VAS)', pill:'pill-slider' };

export const defaultQuestion = {
  type:'slider', prompt:'', label_min:'not at all', label_max:'very strongly',
};

export function renderStudy(q, i) {
  return `
    <div class="q-type-tag"><i class="iconoir-sliders-vertical"></i> ${escapeHtml(t('cards.slider.tag', 'Rating scale'))}</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    ${renderCardInstruction(q)}
    <div class="vas-wrap">
      <div class="vas-track">
        <span class="vas-pole">${escapeHtml(q.label_min||'min')}</span>
        <input class="js-slider-input" data-value-target="vv${i}" type="range" id="q${i}" min="0" max="100" value="50">
        <span class="vas-pole">${escapeHtml(q.label_max||'max')}</span>
      </div>
      <div class="vas-val" id="vv${i}">50</div>
    </div>`;
}

export function renderEditor(q) {
  return `
    <div class="row2">
      <div class="field">
        <label>${escapeHtml(t('editor.leftLabelMin', 'Left label (min)'))}</label>
        <input type="text" class="qe-lmin" value="${escapeHtml(q.label_min||'')}">
      </div>
      <div class="field">
        <label>${escapeHtml(t('editor.rightLabelMax', 'Right label (max)'))}</label>
        <input type="text" class="qe-lmax" value="${escapeHtml(q.label_max||'')}">
      </div>
    </div>`;
}

export function collectConfig(el) {
  return {
    type: 'slider',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    label_min: el.querySelector('.qe-lmin')?.value.trim() || '',
    label_max: el.querySelector('.qe-lmax')?.value.trim() || '',
  };
}

export function collectAnswer(i) {
  return Number.parseInt(document.getElementById(`q${i}`)?.value, 10) ?? 50;
}

// Called by study-controller event listener
export function onInput(event) {
  const slider = event.target.closest('.js-slider-input');
  if (!slider) return false;
  const target = document.getElementById(slider.dataset.valueTarget);
  if (target) target.textContent = slider.value;
  return true;
}
