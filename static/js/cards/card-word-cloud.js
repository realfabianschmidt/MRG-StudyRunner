function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const meta = { type: 'word-cloud', icon: 'chat-bubble', label: 'Word Cloud', pill: 'pill-word-cloud' };

const DEFAULT_WORDS = ['Happy','Sad','Excited','Calm','Anxious','Tired','Focused','Restless'];

export const defaultQuestion = {
  type: 'word-cloud',
  prompt: 'Which words describe how you feel right now?',
  words: DEFAULT_WORDS.slice(),
  allow_multiple: true,
};

// Per-card selection state: { [cardIndex]: Set<string> }
const _selected = {};

function getSelected(i) {
  if (!_selected[i]) _selected[i] = new Set();
  return _selected[i];
}

// ── Render ───────────────────────────────────────────────────────────────────

export function renderStudy(q, i) {
  const words = q.words?.length ? q.words : DEFAULT_WORDS;
  const isMultiple = q.allow_multiple !== false;

  // Clear any prior state for this card
  _selected[i] = new Set();

  const chips = words.map(w => `
    <button type="button"
            class="wc-chip"
            data-card-index="${i}"
            data-word="${escapeHtml(w)}"
            aria-pressed="false">
      ${escapeHtml(w)}
    </button>`).join('');

  return `
    <div class="q-type-tag"><i class="iconoir-chat-bubble"></i> Word Cloud</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    <div class="wc-cloud" id="wc-cloud-${i}" data-multiple="${isMultiple}" role="group"
         aria-label="${escapeHtml(q.prompt)}">${chips}</div>
    <div class="wc-tray" id="wc-tray-${i}" aria-label="Selected words">
      <span class="wc-tray-hint" id="wc-tray-hint-${i}">
        Tap a word — or drag it here
      </span>
    </div>`;
}

export function renderEditor(q) {
  const words = (q.words ?? DEFAULT_WORDS).join('\n');
  return `
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="Which words describe how you feel?">
    </div>
    <div class="field">
      <label>Words (one per line)</label>
      <textarea class="qe-words fi-textarea" style="min-height:100px;">${escapeHtml(words)}</textarea>
    </div>
    <div class="field">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal;">
        <input type="checkbox" class="qe-allow-multiple" ${q.allow_multiple !== false ? 'checked' : ''}>
        Allow selecting multiple words
      </label>
    </div>`;
}

export function collectConfig(el) {
  const raw = el.querySelector('.qe-words')?.value || '';
  const words = raw.split('\n').map(l => l.trim()).filter(Boolean);
  return {
    type: 'word-cloud',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    words: words.length ? words : DEFAULT_WORDS.slice(),
    allow_multiple: el.querySelector('.qe-allow-multiple')?.checked !== false,
  };
}

export function collectAnswer(i) {
  const sel = getSelected(i);
  return sel.size > 0 ? Array.from(sel) : null;
}

// ── Interaction (delegated from study-controller via pointer events) ──────────
// study-controller calls bindWordCloudEvents after rendering each card.

export function bindCardEvents(cardEl, cardIndex) {
  const cloud = cardEl.querySelector(`#wc-cloud-${cardIndex}`);
  const tray  = cardEl.querySelector(`#wc-tray-${cardIndex}`);
  if (!cloud || !tray) return;

  const isMultiple = cloud.dataset.multiple !== 'false';

  // ── Drag ghost element (shared, removed on drop) ──
  let ghost = null;
  let dragging = null;   // { chip, word, startX, startY, moved }
  let trayHighlit = false;

  function createGhost(chip, x, y) {
    const g = document.createElement('span');
    g.className = 'wc-drag-ghost';
    g.textContent = chip.dataset.word;
    g.style.left = x + 'px';
    g.style.top  = y + 'px';
    document.body.appendChild(g);
    return g;
  }

  function moveGhost(x, y) {
    if (!ghost) return;
    ghost.style.left = x + 'px';
    ghost.style.top  = y + 'px';
  }

  function removeGhost() {
    ghost?.remove();
    ghost = null;
  }

  function isOverTray(x, y) {
    const r = tray.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  function setTrayHighlight(on) {
    if (on === trayHighlit) return;
    trayHighlit = on;
    tray.classList.toggle('wc-tray--over', on);
  }

  // ── Toggle a word selection ──
  function toggleWord(word, chip) {
    const sel = getSelected(cardIndex);
    if (sel.has(word)) {
      sel.delete(word);
      chip.setAttribute('aria-pressed', 'false');
      chip.classList.remove('wc-chip--selected');
      removeTrayChip(tray, word, cardIndex);
    } else {
      if (!isMultiple) {
        // Clear existing selection
        sel.forEach(w => {
          const c = cloud.querySelector(`[data-word="${CSS.escape(w)}"]`);
          if (c) { c.classList.remove('wc-chip--selected'); c.setAttribute('aria-pressed', 'false'); }
          removeTrayChip(tray, w, cardIndex);
        });
        sel.clear();
      }
      sel.add(word);
      chip.setAttribute('aria-pressed', 'true');
      chip.classList.add('wc-chip--selected');
      addTrayChip(tray, word, cardIndex, cloud, isMultiple);
    }
    updateTrayHint(tray, cardIndex);
    cloud.dispatchEvent(new CustomEvent('wordcloud:changed', { bubbles: true }));
  }

  // ── Pointer events on the cloud ──
  cloud.addEventListener('pointerdown', e => {
    const chip = e.target.closest('.wc-chip');
    if (!chip) return;

    dragging = {
      chip,
      word: chip.dataset.word,
      startX: e.clientX,
      startY: e.clientY,
      moved: false,
    };
    chip.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  cloud.addEventListener('pointermove', e => {
    if (!dragging) return;
    const dx = e.clientX - dragging.startX;
    const dy = e.clientY - dragging.startY;

    if (!dragging.moved && Math.hypot(dx, dy) > 10) {
      // Start dragging — spawn ghost
      dragging.moved = true;
      ghost = createGhost(dragging.chip, e.clientX, e.clientY);
      dragging.chip.classList.add('wc-chip--dragging');
    }

    if (dragging.moved) {
      moveGhost(e.clientX, e.clientY);
      setTrayHighlight(isOverTray(e.clientX, e.clientY));
    }
  });

  cloud.addEventListener('pointerup', e => {
    if (!dragging) return;
    const { chip, word, moved } = dragging;
    dragging = null;

    chip.classList.remove('wc-chip--dragging');

    if (moved) {
      removeGhost();
      setTrayHighlight(false);
      if (isOverTray(e.clientX, e.clientY)) {
        // Dropped on tray → select if not already
        const sel = getSelected(cardIndex);
        if (!sel.has(word)) toggleWord(word, chip);
      }
    } else {
      // Tap (no drag) → toggle
      toggleWord(word, chip);
    }
  });

  cloud.addEventListener('pointercancel', () => {
    if (!dragging) return;
    dragging.chip.classList.remove('wc-chip--dragging');
    dragging = null;
    removeGhost();
    setTrayHighlight(false);
  });
}

// ── Tray helpers ──────────────────────────────────────────────────────────────

function addTrayChip(tray, word, cardIndex, cloud, isMultiple) {
  const existing = tray.querySelector(`[data-word="${CSS.escape(word)}"]`);
  if (existing) return;

  const chip = document.createElement('span');
  chip.className = 'wc-tray-chip';
  chip.dataset.word = word;
  chip.innerHTML = `${escapeHtml(word)}<button class="wc-tray-remove" aria-label="Remove ${escapeHtml(word)}">×</button>`;

  chip.querySelector('.wc-tray-remove').addEventListener('click', () => {
    const sel = getSelected(cardIndex);
    sel.delete(word);
    chip.remove();
    // Un-highlight the source chip
    const sourceChip = cloud?.querySelector(`[data-word="${CSS.escape(word)}"]`);
    if (sourceChip) {
      sourceChip.classList.remove('wc-chip--selected');
      sourceChip.setAttribute('aria-pressed', 'false');
    }
    updateTrayHint(tray, cardIndex);
  });

  tray.appendChild(chip);
}

function removeTrayChip(tray, word) {
  tray.querySelector(`[data-word="${CSS.escape(word)}"]`)?.remove();
}

function updateTrayHint(tray, cardIndex) {
  const hint = tray.querySelector(`#wc-tray-hint-${cardIndex}`);
  if (!hint) return;
  hint.style.display = getSelected(cardIndex).size > 0 ? 'none' : '';
}
