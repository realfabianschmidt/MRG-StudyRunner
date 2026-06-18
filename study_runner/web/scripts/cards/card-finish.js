export function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export const meta = {
  type: 'finish',
  icon: 'check-circle',
  label: 'End Screen',
  pill: 'pill-finish',
};

export const defaultQuestion = {
  type: 'finish',
  title: 'Vielen Dank!',
  prompt: 'Deine Antworten wurden gespeichert.\nDu kannst das Gerät jetzt ablegen.',
};

export function renderStudy(q, _i) {
  return `
    <div class="q-type-tag"><i class="iconoir-check-circle"></i> Ende</div>
    <div class="done-icon" style="margin-top: 40px; margin-bottom: 24px;"><i class="iconoir-check"></i></div>
    <h1 class="screen-title" style="text-align: center;">${escapeHtml(q.title || defaultQuestion.title)}</h1>
    <p class="screen-sub" style="text-align: center; white-space: pre-wrap;">${escapeHtml(q.prompt || defaultQuestion.prompt)}</p>
  `;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>Überschrift</label>
      <input type="text" class="fi-input q-title-input" value="${escapeHtml(q.title || defaultQuestion.title)}">
    </div>
    <div class="field">
      <label>Nachricht (Untertitel)</label>
      <textarea class="fi-textarea q-prompt-input" rows="3">${escapeHtml(q.prompt || defaultQuestion.prompt)}</textarea>
    </div>
    <p class="editor-hint" style="margin-top:0.75rem;font-size:0.8rem;opacity:0.6;">
      Diese Karte wird automatisch am Ende der Studie angezeigt, nachdem die Daten erfolgreich gespeichert wurden.
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
