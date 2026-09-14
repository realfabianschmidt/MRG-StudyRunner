// Example question-card browser module -- Study Runner plugin SDK template.
//
// Rename the question type ('example-question') here, in manifest.json, and
// in plugin.py before shipping -- it must be globally unique across every
// card plugin, so the SDK's `new` command does not rename it for you.
// This file is a browser (ES module) asset: tools/plugin_sdk.py's
// Python-only `validate`/`check-runtime` commands do not exercise it.
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { renderStudyHeader } from '/static/scripts/cards/card-info.js';

export const meta = { type: 'example-question', icon: 'text', label: 'Example question', pill: 'pill-example' };

export function renderStudy(q, i) {
  return `
    ${renderStudyHeader(q, { icon: 'text', tagKey: 'cards.example.tag', tagFallback: 'Free text' })}
    <textarea id="q${i}" class="fi-textarea example-card-input"></textarea>`;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>Prompt</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt || '')}">
    </div>`;
}

export function collectConfig(el) {
  return {
    type: 'example-question',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
  };
}

export function collectAnswer(i) {
  return document.getElementById(`q${i}`)?.value.trim() || '';
}

export function isAnswered(_question, _questionIndex, { touchedFieldCount }) {
  return touchedFieldCount >= 1;
}

export let defaultQuestion;
export function configureCard(defaults) { defaultQuestion = defaults['example-question']; }
export const metaByType = { 'example-question': meta };
