function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const meta = { type: 'multi-slider', icon: 'sliders-vertical', label: 'Multi-Slider', pill: 'pill-multi-slider' };

export const defaultQuestion = {
  type: 'multi-slider',
  prompt: 'Rate how you feel on each dimension:',
  dimensions: [
    { label: 'Valence',  min_label: 'Very negative', max_label: 'Very positive' },
    { label: 'Arousal',  min_label: 'Very calm',     max_label: 'Very excited'  },
  ],
};

export function renderStudy(q, i) {
  const dims = q.dimensions ?? defaultQuestion.dimensions;
  const rows = dims.map((dim, di) => {
    const vid = `ms-val-${i}-${di}`;
    return `
      <div class="ms-dimension">
        <span class="ms-dim-label">${escapeHtml(dim.label)}</span>
        <div class="ms-track-wrap">
          <span class="ms-pole ms-pole--left">${escapeHtml(dim.min_label || '')}</span>
          <div class="ms-track-inner">
            <input class="js-slider-input ms-range"
                   type="range" min="-100" max="100" value="0"
                   id="ms-${i}-${di}"
                   data-value-target="${vid}"
                   aria-label="${escapeHtml(dim.label)}">
            <div class="ms-neutral-tick" aria-hidden="true"></div>
          </div>
          <span class="ms-pole ms-pole--right">${escapeHtml(dim.max_label || '')}</span>
        </div>
        <div class="ms-val-row">
          <span class="ms-val" id="${vid}">0</span>
          <span class="ms-val-unit">±100</span>
        </div>
      </div>`;
  }).join('');

  return `
    <div class="q-type-tag"><i class="iconoir-sliders-vertical"></i> Multi-Slider</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    <div class="ms-stack">${rows}</div>`;
}

export function renderEditor(q) {
  const dims = q.dimensions ?? defaultQuestion.dimensions;
  const dimRows = dims.map((dim, di) => `
    <div class="ms-ed-dim-row" data-di="${di}">
      <input type="text" class="ms-ed-label fi-input" value="${escapeHtml(dim.label)}" placeholder="Dimension name" style="margin-bottom:0;">
      <input type="text" class="ms-ed-lmin fi-input" value="${escapeHtml(dim.min_label||'')}" placeholder="Left label" style="margin-bottom:0;">
      <input type="text" class="ms-ed-lmax fi-input" value="${escapeHtml(dim.max_label||'')}" placeholder="Right label" style="margin-bottom:0;">
      <button type="button" class="ms-ed-remove-btn" aria-label="Remove dimension">✕</button>
    </div>`).join('');

  return `
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="Rate how you feel...">
    </div>
    <div class="field">
      <label>Dimensions</label>
      <div class="ms-ed-dims-header">
        <span>Name</span><span>Left label</span><span>Right label</span><span></span>
      </div>
      <div class="ms-ed-dims">${dimRows}</div>
      <button type="button" class="ms-ed-add-btn">+ Add dimension</button>
    </div>`;
}

export function collectConfig(el) {
  const dims = [];
  el.querySelectorAll('.ms-ed-dim-row').forEach(row => {
    const label    = row.querySelector('.ms-ed-label')?.value.trim() || '';
    const min_label = row.querySelector('.ms-ed-lmin')?.value.trim()  || '';
    const max_label = row.querySelector('.ms-ed-lmax')?.value.trim()  || '';
    if (label) dims.push({ label, min_label, max_label });
  });
  return {
    type: 'multi-slider',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    dimensions: dims.length ? dims : defaultQuestion.dimensions,
  };
}

export function collectAnswer(i, q) {
  const dims = q.dimensions ?? defaultQuestion.dimensions;
  const result = {};
  dims.forEach((dim, di) => {
    const el = document.getElementById(`ms-${i}-${di}`);
    result[dim.label] = el ? Number.parseInt(el.value, 10) : 0;
  });
  return result;
}

// Admin editor: wire up add/remove buttons (called by admin controller after injecting editor HTML)
export function bindEditorEvents(el) {
  el.querySelector('.ms-ed-add-btn')?.addEventListener('click', () => {
    const container = el.querySelector('.ms-ed-dims');
    if (!container) return;
    const di = container.querySelectorAll('.ms-ed-dim-row').length;
    const row = document.createElement('div');
    row.className = 'ms-ed-dim-row';
    row.dataset.di = di;
    row.innerHTML = `
      <input type="text" class="ms-ed-label fi-input" value="" placeholder="Dimension name" style="margin-bottom:0;">
      <input type="text" class="ms-ed-lmin  fi-input" value="" placeholder="Left label"      style="margin-bottom:0;">
      <input type="text" class="ms-ed-lmax  fi-input" value="" placeholder="Right label"     style="margin-bottom:0;">
      <button type="button" class="ms-ed-remove-btn" aria-label="Remove dimension">✕</button>`;
    container.appendChild(row);
    row.querySelector('.ms-ed-remove-btn').addEventListener('click', () => row.remove());
  });

  el.querySelectorAll('.ms-ed-remove-btn').forEach(btn => {
    btn.addEventListener('click', () => btn.closest('.ms-ed-dim-row')?.remove());
  });
}
