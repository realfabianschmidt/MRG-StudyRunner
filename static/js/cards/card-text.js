function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const meta = { type:'text', icon:'chat-bubble', label:'Free text', pill:'pill-text' };

export const defaultQuestion = { type:'text', prompt:'' };

export function renderStudy(q, i) {
  return `
    <div class="q-type-tag"><i class="iconoir-chat-bubble"></i> Free text</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    <textarea class="fi-textarea" id="q${i}" placeholder="Your answer..."></textarea>`;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="Enter question...">
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
