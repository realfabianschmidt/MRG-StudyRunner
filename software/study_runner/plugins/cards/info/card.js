import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import { renderMediaLayout } from '/static/scripts/shared/rich-text.js';
import { bindMediaEditor, collectMediaEditor, renderMediaEditor } from '/static/scripts/shared/media-editor.js';

export { escapeHtml };

export const meta = {
  type: 'info',
  icon: 'page',
  label: 'Information',
};

export function renderStudy(q, _i) {
  const content = { ...defaultQuestion, ...q };
  return `
    <div class="q-type-tag"><i class="iconoir-page"></i> ${escapeHtml(t('cards.info.tag', 'Information'))}</div>
    <div class="info-card">${renderMediaLayout(content)}</div>
  `;
}

export function renderEditor(q) {
  return `${renderMediaEditor({ ...defaultQuestion, ...q })}
    <p class="editor-hint" style="margin-top:0.75rem;font-size:0.8rem;opacity:0.6;">
      ${escapeHtml(t('cards.info.editorHint', 'The participant reads this card and continues with Next. It records no answer.'))}
    </p>`;
}

export function bindEditorEvents(el) {
  bindMediaEditor(el);
}

export function collectConfig(el) {
  return { type: 'info', ...collectMediaEditor(el) };
}

export function collectAnswer() {
  return null;
}

export let defaultQuestion;
export function configureCard(defaults) { defaultQuestion = defaults['info']; }
meta.hideSharedPrompt = true;
export const metaByType = { 'info': meta };
