function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const meta = { type:'ranking', icon:'sort', label:'Ranking', pill:'pill-ranking' };

export const defaultQuestion = {
  type: 'ranking', prompt: '', options: ['Item A', 'Item B', 'Item C'],
};

export function renderStudy(q, i) {
  const itemsHtml = (q.options || []).map((opt, optionIndex) => `
    <div class="rank-item">
      <span class="rank-handle" aria-hidden="true"><i class="iconoir-menu-scale"></i></span>
      <span class="rank-num">#${optionIndex + 1}</span>
      <span class="rank-text">${escapeHtml(opt)}</span>
    </div>`).join('');

  return `
    <div class="q-type-tag"><i class="iconoir-sort"></i> Ranking</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    <div class="rank-list" id="rl${i}">${itemsHtml}</div>`;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="Enter question...">
    </div>
    <div class="field">
      <label>Items (one per line)</label>
      <textarea class="qe-options">${escapeHtml((q.options || []).join('\n'))}</textarea>
    </div>`;
}

export function collectConfig(el) {
  const options = (el.querySelector('.qe-options')?.value || '')
    .split('\n').map(line => line.trim()).filter(Boolean);
  return {
    type: 'ranking',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    options,
  };
}

export function collectAnswer(questionIndex) {
  const list = document.getElementById(`rl${questionIndex}`);
  return [...(list?.children || [])].map(el => el.querySelector('.rank-text').textContent);
}

function renumberItems(list) {
  [...list.querySelectorAll('.rank-item')].forEach((el, idx) => {
    const num = el.querySelector('.rank-num');
    if (num) num.textContent = `#${idx + 1}`;
  });
}

export function bindDrag(list) {
  let dragEl = null;
  let ghost  = null;
  let offsetY = 0;

  list.addEventListener('pointerdown', e => {
    const handle = e.target.closest('.rank-handle');
    if (!handle) return;
    const item = handle.closest('.rank-item');
    if (!item) return;

    e.preventDefault();
    const rect = item.getBoundingClientRect();
    offsetY = e.clientY - rect.top;

    // Floating visual clone
    ghost = item.cloneNode(true);
    ghost.classList.add('rank-item--ghost');
    ghost.style.cssText =
      `position:fixed;left:${rect.left}px;top:${rect.top}px;` +
      `width:${rect.width}px;z-index:9999;pointer-events:none;margin:0;`;
    document.body.appendChild(ghost);

    dragEl = item;
    item.classList.add('rank-item--dragging');
    list.setPointerCapture(e.pointerId);
  });

  list.addEventListener('pointermove', e => {
    if (!ghost || !dragEl) return;
    ghost.style.top = (e.clientY - offsetY) + 'px';

    // Reorder: find the first non-dragging item whose midpoint is below the pointer
    const siblings = [...list.querySelectorAll('.rank-item:not(.rank-item--dragging)')];
    let insertBefore = null;
    for (const sib of siblings) {
      const r = sib.getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) { insertBefore = sib; break; }
    }
    if (insertBefore) {
      list.insertBefore(dragEl, insertBefore);
    } else {
      list.appendChild(dragEl);
    }
  });

  const finish = () => {
    if (!ghost) return;
    ghost.remove();
    ghost = null;
    dragEl.classList.remove('rank-item--dragging');
    dragEl = null;
    renumberItems(list);
    list.dispatchEvent(new CustomEvent('ranking:changed', { bubbles: true }));
  };

  list.addEventListener('pointerup',     finish);
  list.addEventListener('pointercancel', finish);
}
