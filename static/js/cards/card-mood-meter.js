function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const meta = { type: 'mood-meter', icon: 'app-window', label: 'Mood Meter', pill: 'pill-mood-meter' };

// Brackett Mood Meter quadrant definitions
// Order matters: Red → Yellow → Green → Blue (clockwise, used for prev/next navigation)
const QUADRANTS = [
  {
    id: 'red',
    label: 'High Energy · Unpleasant',
    color: '#C0392B', colorDark: '#7B241C',
    dirX: -1, dirY: -1,
    examples: ['Stressed', 'Anxious', 'Frustrated'],
    words: [
      'Enraged','Panicked','Stressed','Jittery','Shocked',
      'Livid','Furious','Frustrated','Tense','Stunned',
      'Fuming','Frightened','Angry','Nervous','Restless',
      'Anxious','Apprehensive','Worried','Irritated','Annoyed',
      'Repulsed','Troubled','Concerned','Uneasy','Peeved',
    ],
  },
  {
    id: 'yellow',
    label: 'High Energy · Pleasant',
    color: '#D4860A', colorDark: '#9A6108',
    dirX: 1, dirY: -1,
    examples: ['Excited', 'Happy', 'Enthusiastic'],
    words: [
      'Surprised','Upbeat','Festive','Exhilarated','Ecstatic',
      'Hyper','Cheerful','Motivated','Inspired','Elated',
      'Energized','Lively','Excited','Optimistic','Enthusiastic',
      'Pleased','Focused','Happy','Proud','Thrilled',
      'Pleasant','Joyful','Hopeful','Playful','Blissful',
    ],
  },
  {
    id: 'green',
    label: 'Low Energy · Pleasant',
    color: '#1E8449', colorDark: '#145A32',
    dirX: 1, dirY: 1,
    examples: ['Calm', 'Content', 'Serene'],
    words: [
      'At Ease','Easygoing','Content','Loving','Fulfilled',
      'Calm','Secure','Satisfied','Grateful','Touched',
      'Relaxed','Chill','Restful','Blessed','Balanced',
      'Mellow','Thoughtful','Peaceful','Comfortable','Carefree',
      'Sleepy','Complacent','Tranquil','Cozy','Serene',
    ],
  },
  {
    id: 'blue',
    label: 'Low Energy · Unpleasant',
    color: '#2471A3', colorDark: '#1A5276',
    dirX: -1, dirY: 1,
    examples: ['Sad', 'Tired', 'Hopeless'],
    words: [
      'Disgusted','Glum','Disappointed','Down','Apathetic',
      'Pessimistic','Morose','Discouraged','Sad','Bored',
      'Alienated','Miserable','Lonely','Disheartened','Tired',
      'Despondent','Depressed','Sullen','Exhausted','Fatigued',
      'Despair','Hopeless','Desolate','Spent','Drained',
    ],
  },
];

// Per-card state: { [cardIndex]: { selected: Set<string> } }
const _state = {};
// Per-card question config cache (set in renderStudy)
const _questions = {};

function getState(i) {
  if (!_state[i]) _state[i] = { selected: new Set() };
  return _state[i];
}

function getWordLists(q) {
  if (q?.word_lists) {
    return QUADRANTS.map(qd => ({ ...qd, words: q.word_lists[qd.id] ?? qd.words }));
  }
  return QUADRANTS;
}

export const defaultQuestion = {
  type: 'mood-meter',
  prompt: 'How do you feel right now?',
  allow_multiple: true,
  word_lists: null,
};

export function renderStudy(q, i) {
  _questions[i] = q;
  const quads = getWordLists(q);

  const tiles = quads.map(quad => {
    return `
    <button class="mm-quad-btn" data-card-index="${i}" data-quadrant="${quad.id}"
            style="--mm-color:${quad.color};">
      <span class="mm-quad-label">${escapeHtml(quad.label)}</span>
      <span class="mm-quad-examples">${quad.examples.slice(0, 3).map(w => escapeHtml(w)).join(' · ')}</span>
    </button>`;
  }).join('');

  return `
    <div class="q-type-tag"><i class="iconoir-app-window"></i> Mood Meter</div>
    <p class="q-prompt">${escapeHtml(q.prompt)}</p>
    <div class="mm-grid" id="mm-grid-${i}">${tiles}</div>`;
}

export function renderEditor(q) {
  const wordListsUsed = getWordLists(q);
  const quadSections = wordListsUsed.map(quad => `
    <div class="field mm-ed-quad-field">
      <label class="mm-ed-quad-label" style="color:${quad.color};">${escapeHtml(quad.label)}</label>
      <textarea class="mm-ed-words fi-textarea" data-quadrant="${quad.id}"
                style="min-height:96px;font-size:.75rem;"
                placeholder="One word per line">${escapeHtml(quad.words.join('\n'))}</textarea>
    </div>`).join('');

  return `
    <div class="field">
      <label>Question text</label>
      <input type="text" class="qe-prompt" value="${escapeHtml(q.prompt)}" placeholder="How do you feel right now?">
    </div>
    <div class="field">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal;">
        <input type="checkbox" class="qe-allow-multiple" ${q.allow_multiple !== false ? 'checked' : ''}>
        Allow selecting multiple words
      </label>
    </div>
    <div class="mm-ed-quads">${quadSections}</div>`;
}

export function collectConfig(el) {
  const word_lists = {};
  el.querySelectorAll('.mm-ed-words').forEach(ta => {
    const quadId = ta.dataset.quadrant;
    const words = ta.value.split('\n').map(l => l.trim()).filter(Boolean);
    const defaultQuad = QUADRANTS.find(q => q.id === quadId);
    if (defaultQuad && JSON.stringify(words) !== JSON.stringify(defaultQuad.words)) {
      word_lists[quadId] = words;
    }
  });
  const hasCustomWords = Object.keys(word_lists).length > 0;
  return {
    type: 'mood-meter',
    prompt: el.querySelector('.qe-prompt')?.value.trim() || '',
    allow_multiple: el.querySelector('.qe-allow-multiple')?.checked !== false,
    word_lists: hasCustomWords ? word_lists : null,
  };
}

export function collectAnswer(i) {
  const sel = getState(i).selected;
  return sel.size > 0 ? Array.from(sel) : null;
}

// Called by study-controller's delegated click handler on #q-container
export function onClick(event) {
  const btn = event.target.closest('.mm-quad-btn');
  if (!btn) return false;
  const cardIndex = Number(btn.dataset.cardIndex);
  const quadId = btn.dataset.quadrant;
  openOverlay(cardIndex, quadId, btn.getBoundingClientRect());
  return true;
}

// ── Overlay ──────────────────────────────────────────────────────────────────

let activeOverlay = null;

// Hex layout constants
const CELL_W = 130, CELL_H = 96, ROWS = 5, COLS = 5;
const MARGIN_X = 180, MARGIN_Y = 140;
const QX = ((COLS - 1) * CELL_W) / 2 + MARGIN_X;
const QY = ((ROWS - 1) * CELL_H) / 2 + MARGIN_Y;

function buildHexPositions() {
  const pos = [];
  const offsetX = -((COLS - 1) * CELL_W + CELL_W / 2) / 2;
  const offsetY = -((ROWS - 1) * CELL_H) / 2;
  for (let r = 0; r < ROWS; r++) {
    const shiftX = (r % 2) * (CELL_W / 2);
    for (let c = 0; c < COLS; c++) {
      pos.push({ x: c * CELL_W + shiftX + offsetX, y: r * CELL_H + offsetY });
    }
  }
  return pos;
}

function updateBubbleSizes(viewport, space, panX, panY) {
  const vcx = viewport.clientWidth  / 2;
  const vcy = viewport.clientHeight / 2;
  const maxDist = Math.hypot(vcx, vcy) * 1.5;

  space.querySelectorAll('.mm-word').forEach(btn => {
    const bx = parseFloat(btn.style.left) + panX;
    const by = parseFloat(btn.style.top)  + panY;
    let t = Math.hypot(bx - vcx, by - vcy) / (maxDist * 0.8);
    if (t > 1.2) t = 1.2;
    
    const scale = Math.max(0.1, 1.65 - t * 1.15);
    const alpha = Math.max(0, 1.0 - t * 0.8);
    
    btn.style.setProperty('--mm-scale', scale.toFixed(3));
    btn.style.opacity = alpha.toFixed(3);
    btn.style.visibility = alpha === 0 ? 'hidden' : 'visible';
  });
}

function buildBubbleSpace(space, quads, selectedSet) {
  space.replaceChildren();
  const positions = buildHexPositions();
  quads.forEach(quad => {
    const cx = quad.dirX * QX;
    const cy = quad.dirY * QY;
    quad.words.forEach((w, wi) => {
      if (wi >= positions.length) return;
      const btn = document.createElement('button');
      btn.className = 'mm-word' + (selectedSet.has(w) ? ' mm-word--selected' : '');
      btn.dataset.word = w;
      btn.textContent = w;
      btn.style.left = (cx + positions[wi].x) + 'px';
      btn.style.top  = (cy + positions[wi].y) + 'px';
      space.appendChild(btn);
    });
  });
}

function setupPan(overlay, viewport, space, initPanX, initPanY, cardIndex, allowMultiple, quads, initialQuadId) {
  let panX = initPanX, panY = initPanY;
  let velX = 0, velY = 0;
  let downX = 0, downY = 0, lastX = 0, lastY = 0;
  let isPanning = false;
  let rafId = null;
  let activeQuadId = initialQuadId;

  const limit = (val, min, max) => Math.max(min, Math.min(max, val));
  const BOUND_X = QX + 300;
  const BOUND_Y = QY + 300;

  const applyTransform = () => {
    space.style.transform = `translate(${panX}px, ${panY}px)`;
    updateBubbleSizes(viewport, space, panX, panY);

    // Calculate which quadrant we are looking at
    const scX = (viewport.clientWidth / 2) - panX;
    const scY = (viewport.clientHeight / 2) - panY;
    const dirX = scX < 0 ? -1 : 1;
    const dirY = scY < 0 ? -1 : 1;

    const currentQuad = quads.find(q => q.dirX === dirX && q.dirY === dirY);
    if (currentQuad && currentQuad.id !== activeQuadId) {
      activeQuadId = currentQuad.id;
      
      // Crossfade Backgrounds
      overlay.querySelectorAll('.mm-bg').forEach(bg => {
        bg.style.opacity = bg.classList.contains(`mm-bg-${activeQuadId}`) ? '1' : '0';
      });

      // Swap Title Text
      const titleEl = overlay.querySelector('.mm-overlay-title');
      if (titleEl) {
        titleEl.style.opacity = '0';
        setTimeout(() => {
          titleEl.textContent = currentQuad.label;
          titleEl.style.opacity = '1';
        }, 150);
      }
    }
  };

  applyTransform();

  viewport.addEventListener('pointerdown', e => {
    velX = velY = 0;
    downX = lastX = e.clientX;
    downY = lastY = e.clientY;
    isPanning = true;
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    viewport.setPointerCapture(e.pointerId);
  });

  viewport.addEventListener('pointermove', e => {
    if (!isPanning) return;
    velX = e.clientX - lastX;
    velY = e.clientY - lastY;
    panX = limit(panX + velX, viewport.clientWidth/2 - BOUND_X, viewport.clientWidth/2 + BOUND_X);
    panY = limit(panY + velY, viewport.clientHeight/2 - BOUND_Y, viewport.clientHeight/2 + BOUND_Y);
    lastX = e.clientX; lastY = e.clientY;
    applyTransform();
  });

  viewport.addEventListener('pointerup', e => {
    if (!isPanning) return;
    isPanning = false;
    const dx = e.clientX - downX;
    const dy = e.clientY - downY;
    const moved = Math.hypot(dx, dy);

    if (moved < 8) {
      // Tap: toggle word
      const word = e.target.closest('.mm-word');
      if (word) toggleWord(cardIndex, word.dataset.word, allowMultiple, overlay);
    } else {
      // Momentum scroll
      (function momentum() {
        if (Math.abs(velX) < 0.4 && Math.abs(velY) < 0.4) return;
        velX *= 0.92; velY *= 0.92;
        panX = limit(panX + velX, viewport.clientWidth/2 - BOUND_X, viewport.clientWidth/2 + BOUND_X);
        panY = limit(panY + velY, viewport.clientHeight/2 - BOUND_Y, viewport.clientHeight/2 + BOUND_Y);
        applyTransform();
        rafId = requestAnimationFrame(momentum);
      })();
    }
  });
}

function openOverlay(cardIndex, quadId, originRect) {
  if (activeOverlay) closeOverlay(false);

  const q = _questions[cardIndex] ?? defaultQuestion;
  const quads = getWordLists(q);
  const initialQuad = quads.find(qd => qd.id === quadId) || quads[0];
  const allowMultiple = q.allow_multiple !== false;
  const state = getState(cardIndex);

  const overlay = document.createElement('div');
  overlay.id = 'mm-overlay';
  overlay.style.background = '#111'; // Base dark space

  // Render 4 large background gradients that we crossfade
  const backgrounds = quads.map(qd => `
    <div class="mm-bg mm-bg-${qd.id}"
         style="position:absolute;inset:0;background:linear-gradient(145deg, ${qd.color} 0%, ${qd.colorDark} 100%);
         opacity:${qd.id === initialQuad.id ? '1' : '0'};transition:opacity 0.8s ease;"></div>
  `).join('');

  overlay.innerHTML = `
    ${backgrounds}
    <button class="mm-back-btn" aria-label="Back to overview">
      <i class="iconoir-arrow-left"></i>
    </button>
    <div class="mm-float-header">
      <span class="mm-overlay-title" style="transition: opacity 0.3s ease;">${escapeHtml(initialQuad.label)}</span>
      <span class="mm-sel-counter" id="mm-sel-counter">${state.selected.size > 0 ? state.selected.size + ' selected' : ''}</span>
    </div>
    <div id="mm-bubble-viewport"><div id="mm-bubble-space"></div></div>`;

  overlay._cardIndex = cardIndex;
  overlay._allowMultiple = allowMultiple;

  // Zoom-in from origin button centre
  const cx = originRect.left + originRect.width / 2;
  const cy = originRect.top  + originRect.height / 2;
  overlay.style.transformOrigin = `${cx}px ${cy}px`;
  overlay.style.transform = 'scale(0.06)';
  overlay.style.opacity = '0';

  document.body.appendChild(overlay);
  activeOverlay = overlay;

  // Build initial bubble space
  const viewport = overlay.querySelector('#mm-bubble-viewport');
  const space    = overlay.querySelector('#mm-bubble-space');
  
  buildBubbleSpace(space, quads, state.selected);

  // Center the viewport exactly on the quadrant we clicked in the overview
  const initPanX = (viewport.clientWidth / 2) - (initialQuad.dirX * QX);
  const initPanY = (viewport.clientHeight / 2) - (initialQuad.dirY * QY);

  // Wire pan + tap
  setupPan(overlay, viewport, space, initPanX, initPanY, cardIndex, allowMultiple, quads, initialQuad.id);

  overlay.querySelector('.mm-back-btn').addEventListener('click', () => closeOverlay(true));

  overlay._escHandler = ev => { if (ev.key === 'Escape') closeOverlay(true); };
  document.addEventListener('keydown', overlay._escHandler);

  // Animate in
  requestAnimationFrame(() => {
    overlay.style.transition = 'transform 0.42s cubic-bezier(.16,.85,.20,1), opacity 0.28s ease';
    overlay.style.transform = 'scale(1)';
    overlay.style.opacity = '1';
  });
}

function closeOverlay(animate) {
  const overlay = activeOverlay;
  if (!overlay) return;
  activeOverlay = null;

  if (overlay._escHandler) document.removeEventListener('keydown', overlay._escHandler);

  if (!animate) { overlay.remove(); return; }

  overlay.style.transition = 'transform 0.3s cubic-bezier(.4,0,.8,1), opacity 0.22s ease';
  overlay.style.transform = 'scale(0.06)';
  overlay.style.opacity = '0';
  overlay.addEventListener('transitionend', () => overlay.remove(), { once: true });
}

function toggleWord(cardIndex, word, allowMultiple, overlay) {
  const state = getState(cardIndex);
  if (state.selected.has(word)) {
    state.selected.delete(word);
  } else {
    if (!allowMultiple) state.selected.clear();
    state.selected.add(word);
  }
  // Sync button states in current space only
  const space = overlay.querySelector('#mm-bubble-space');
  if (space) {
    space.querySelectorAll('.mm-word').forEach(btn => {
      btn.classList.toggle('mm-word--selected', state.selected.has(btn.dataset.word));
    });
  }
  const counter = document.getElementById('mm-sel-counter');
  if (counter) {
    const n = state.selected.size;
    counter.textContent = n > 0 ? n + ' selected' : '';
  }
  document.getElementById(`mm-grid-${cardIndex}`)?.dispatchEvent(
    new CustomEvent('moodmeter:changed', { bubbles: true }),
  );
}
