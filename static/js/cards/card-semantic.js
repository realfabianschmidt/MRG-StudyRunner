function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const meta = { type:'semantic', icon:'arrows-horizontal', label:'Semantic', pill:'pill-semantic' };

export const defaultQuestion = {
  type:'semantic', prompt:'', pairs:[['alive','mechanical'],['familiar','unfamiliar']],
};

export function renderStudy(q, i) {
  let pairsHtml = '';
  (q.pairs || []).forEach((pair, pi) => {
    let dots = '';
    for (let d = 1; d <= 7; d++) {
      dots += `<input type="radio" name="q${i}p${pi}" value="${d}" id="q${i}p${pi}d${d}"><label for="q${i}p${pi}d${d}"></label>`;
    }
    pairsHtml += `
      <div class="sem-pair">
        <span class="sem-pole">${escapeHtml(pair[0])}</span>
        <div class="sem-track">${dots}</div>
        <span class="sem-pole">${escapeHtml(pair[1])}</span>
      </div>`;
  });
  return `
    <div class="q-type-tag"><i class="iconoir-arrows-horizontal"></i> Semantic differential</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    ${pairsHtml}`;
}

export function renderEditor(q) {
  const lines = (q.pairs || []).map(p => p.join(' | ')).join('\n');
  return `
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="Enter question...">
    </div>
    <div class="field">
      <label>Word pairs — one per line: word A | word B</label>
      <textarea class="qe-pairs">${escapeHtml(lines)}</textarea>
    </div>`;
}

export function collectConfig(el) {
  const lines = (el.querySelector('.qe-pairs')?.value || '').split('\n').map(l => l.trim()).filter(Boolean);
  const pairs = lines.map(l => l.split('|').map(p => p.trim())).filter(p => p.length === 2);
  return {
    type: 'semantic',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    pairs,
  };
}

export function collectAnswer(i, q) {
  const ans = {};
  (q.pairs || []).forEach((pair, pi) => {
    const sel = document.querySelector(`input[name="q${i}p${pi}"]:checked`);
    ans[`${pair[0]}_${pair[1]}`] = sel ? Number.parseInt(sel.value, 10) : null;
  });
  return ans;
}
