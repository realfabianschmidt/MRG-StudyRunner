import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { renderStudyHeader } from '/static/scripts/cards/card-info.js';

export const meta = { type:'slider', icon:'sliders-vertical', label:'Slider (VAS)', pill:'pill-slider' };


export function renderStudy(q, i) {
  return `
    ${renderStudyHeader(q, { icon: 'control-slider', tagKey: 'cards.slider.tag', tagFallback: 'Rating scale' })}
    <div class="vas-wrap">
      <div class="vas-track">
        <span class="vas-pole">${escapeHtml(q.label_min ?? defaultQuestion.label_min)}</span>
        <input class="js-slider-input" data-value-target="vv${i}" type="range" id="q${i}" min="0" max="100" value="50">
        <span class="vas-pole">${escapeHtml(q.label_max ?? defaultQuestion.label_max)}</span>
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

// A slider defaults to a visible, plausible-looking value (50) whether or
// not the participant ever touched it, so collectAnswer() alone cannot
// tell "answered" from "untouched". touchedFieldCount is the controller's
// own record of real interaction (see study-controller.js's markQuestionField).
export function isAnswered(_question, _questionIndex, { touchedFieldCount }) {
  return touchedFieldCount >= 1;
}

// Called by study-controller event listener
export function onInput(event) {
  const slider = event.target.closest('.js-slider-input');
  if (!slider) return false;
  const target = document.getElementById(slider.dataset.valueTarget);
  if (target) target.textContent = slider.value;
  return true;
}

export let defaultQuestion;
export function configureCard(defaults) { defaultQuestion = defaults['slider']; }
export const metaByType = { 'slider': meta };
