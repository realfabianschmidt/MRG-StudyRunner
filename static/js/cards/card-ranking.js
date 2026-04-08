// Renders a drag-free ranking question where participants move items up or down.
// Each item carries its current position as a data attribute so the onClick
// handler can work without any external state.
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
      <span class="rank-num">#${optionIndex + 1}</span>
      <span class="rank-text">${escapeHtml(opt)}</span>
      <div class="rank-btns">
        <button class="rank-btn" type="button"
          data-role="move-rank"
          data-question-index="${i}"
          data-item-index="${optionIndex}"
          data-direction="-1">
          <i class="iconoir-nav-arrow-up"></i>
        </button>
        <button class="rank-btn" type="button"
          data-role="move-rank"
          data-question-index="${i}"
          data-item-index="${optionIndex}"
          data-direction="1">
          <i class="iconoir-nav-arrow-down"></i>
        </button>
      </div>
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

// Called by study-controller to handle up/down button clicks.
// Returns true when the event was handled, false when it was not a ranking click.
export function onClick(event) {
  const btn = event.target.closest('[data-role="move-rank"]');
  if (!btn) return false;

  const questionIndex = Number(btn.dataset.questionIndex);
  const itemIndex     = Number(btn.dataset.itemIndex);
  const direction     = Number(btn.dataset.direction);
  const list          = document.getElementById(`rl${questionIndex}`);
  if (!list) return true;

  const items      = [...list.children];
  const targetIndex = itemIndex + direction;
  if (targetIndex < 0 || targetIndex >= items.length) return true;

  const [moved] = items.splice(itemIndex, 1);
  items.splice(targetIndex, 0, moved);
  list.replaceChildren();

  // Re-number items and update data attributes so future clicks stay accurate
  items.forEach((el, currentIndex) => {
    el.querySelector('.rank-num').textContent = `#${currentIndex + 1}`;
    el.querySelectorAll('[data-role="move-rank"]').forEach(button => {
      button.dataset.itemIndex = String(currentIndex);
    });
    list.appendChild(el);
  });

  return true;
}
