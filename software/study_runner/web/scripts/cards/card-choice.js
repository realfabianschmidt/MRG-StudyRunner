function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');
}

// Handles both 'choice' (multiple) and 'single'.
// The current schema uses separate card types, so there is no extra toggle.
export const meta = { type: 'choice', icon: 'multi-bubble', label: 'Multiple choice', pill: 'pill-choice' };
export const metaSingle = { type: 'single', icon: 'circle', label: 'Single choice', pill: 'pill-single' };

export const defaultQuestion = {
  type: 'choice', prompt: '', options: ['Option A', 'Option B', 'Option C'],
};
export const defaultQuestionSingle = {
  type: 'single', prompt: '', options: ['Option A', 'Option B', 'Option C'],
};

export function renderStudy(q, i) {
  const isMultiple = q.type === 'choice';
  const inputType = isMultiple ? 'checkbox' : 'radio';
  const icon = isMultiple ? 'multi-bubble' : 'circle';
  const label = isMultiple ? 'Multiple choice' : 'Single choice';
  let opts = '<div class="chips">';
  (q.options || []).forEach((opt, oi) => {
    opts += `<input type="${inputType}" name="q${i}" value="${escapeHtml(opt)}" id="q${i}o${oi}"><label for="q${i}o${oi}">${escapeHtml(opt)}</label>`;
  });
  opts += '</div>';
  return `
    <div class="q-type-tag"><i class="iconoir-${icon}"></i> ${label}</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    ${opts}`;
}

export function renderEditor(q) {
  const options = (q.options || []).join('\n');
  const choiceType = q.type === 'single' ? 'single' : 'choice';
  return `
    <input type="hidden" class="qe-choice-type" value="${choiceType}">
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="Enter question...">
    </div>
    <div class="field">
      <label>Options (one per line)</label>
      <textarea class="qe-options">${escapeHtml(options)}</textarea>
    </div>`;
}

export function collectConfig(el) {
  const choiceType = el.querySelector('.qe-choice-type')?.value === 'single' ? 'single' : 'choice';
  const options = (el.querySelector('.qe-options')?.value || '').split('\n').map(l => l.trim()).filter(Boolean);
  return {
    type: choiceType,
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    options,
  };
}

export function collectAnswer(i, q) {
  if (q.type === 'choice') {
    return [...document.querySelectorAll(`input[name="q${i}"]:checked`)].map(cb => cb.value);
  }
  const sel = document.querySelector(`input[name="q${i}"]:checked`);
  return sel ? sel.value : null;
}
