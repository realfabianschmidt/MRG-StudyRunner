function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

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
    <div class="q-type-tag"><i class="iconoir-list-select"></i> Likert scale</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    <div class="likert-row">${opts}</div>
    <div class="polar-labels"><span>${escapeHtml(q.label_min||'')}</span><span>${escapeHtml(q.label_max||'')}</span></div>`;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="Enter question...">
    </div>
    <div class="row3">
      <div class="field">
        <label>Scale points</label>
        <input type="number" class="qe-scale" value="${q.scale||7}" min="3" max="11">
      </div>
      <div class="field">
        <label>Left label</label>
        <input type="text" class="qe-lmin" value="${escapeHtml(q.label_min||'')}">
      </div>
      <div class="field">
        <label>Right label</label>
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
