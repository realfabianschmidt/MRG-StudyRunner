import { getJson, postJson } from './api-client.js';
import { CARDS, CARD_TYPES, defaultFor } from './cards/index.js';

function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

const state = {
  config:        {},
  selectedIndex: null,
  loaded:        false,
};

const $ = id => document.getElementById(id);

// ── Init ─────────────────────────────────────────────────────

async function init() {
  bindEvents();
  state.config = await getJson('/api/config');
  $('cfg-id').value = state.config.study_id || '';
  rebuildAll();
  state.loaded = true;
  setSavedStatus('Loaded');
}

// ── Type picker overlay ──────────────────────────────────────

function openTypePicker() {
  $('overlay-type-tag').innerHTML = `<i class="iconoir-plus"></i> Add question`;

  $('editor-fields').innerHTML = `
    <div class="type-picker-title">Choose question type</div>
    <div class="type-grid">
      ${CARD_TYPES.map(({ type, module, overrideMeta }) => {
        const meta = overrideMeta || module.meta;
        return `<button type="button" class="type-btn" data-add-type="${escapeHtml(type)}">
          <i class="iconoir-${escapeHtml(meta.icon)}"></i>${escapeHtml(meta.label)}<small>${escapeHtml(type)}</small>
        </button>`;
      }).join('')}
    </div>`;

  $('admin-sidebar').classList.add('has-overlay');
}

// ── Events ───────────────────────────────────────────────────

function bindEvents() {
  $('btn-add-main').addEventListener('click', openTypePicker);
  $('btn-save-config').addEventListener('click', saveConfig);
  $('overlay-close').addEventListener('click', closeOverlay);

  // Handles type-picker buttons and trigger-type pills inside the overlay
  $('sidebar-overlay').addEventListener('click', e => {
    const typeBtn     = e.target.closest('[data-add-type]');
    const triggerPill = e.target.closest('[data-trigger-type]');
    if (typeBtn)     { addQuestion(typeBtn.dataset.addType); return; }
    if (triggerPill) { handleTriggerTypePill(triggerPill);  return; }
  });

  $('admin-q-list').addEventListener('click', handleListClick);

  $('study-preview').addEventListener('click', e => {
    const btn = e.target.closest('[data-role="select-card"]');
    if (btn) selectQuestion(Number(btn.dataset.index));
  });

  // Live preview: any change inside the overlay editor updates the preview card
  $('sidebar-overlay').addEventListener('input', () => {
    if (state.selectedIndex !== null) liveUpdate(state.selectedIndex);
    markUnsaved();
  });

  $('cfg-id').addEventListener('input', markUnsaved);
}

function handleListClick(e) {
  const moveBtn   = e.target.closest('[data-role="move-question"]');
  const removeBtn = e.target.closest('[data-role="remove-question"]');
  const item      = e.target.closest('.admin-q-item');

  if (moveBtn) {
    moveQuestion(Number(moveBtn.dataset.index), Number(moveBtn.dataset.direction));
    return;
  }
  if (removeBtn) {
    removeQuestion(Number(removeBtn.dataset.index));
    return;
  }
  // Clicking the item row (not its action buttons) opens the editor
  if (item && !e.target.closest('.admin-q-actions')) {
    selectQuestion(Number(item.dataset.index));
  }
}

// ── Rebuild ──────────────────────────────────────────────────

function rebuildAll() {
  rebuildList();
  rebuildPreview();
  syncEmptyState();
}

function rebuildList() {
  const list      = $('admin-q-list');
  list.replaceChildren();
  const questions = state.config.questions || [];

  questions.forEach((q, i) => {
    const meta = getMeta(q.type);
    const item = document.createElement('div');
    item.className = 'admin-q-item' + (i === state.selectedIndex ? ' selected' : '');
    item.dataset.index = i;
    item.innerHTML = `
      <span class="admin-q-num">${i + 1}</span>
      <i class="iconoir-${meta.icon} admin-q-type-icon"></i>
      <span class="admin-q-label">${q.prompt ? escapeHtml(q.prompt) : '<em>no text</em>'}</span>
      <div class="admin-q-actions">
        <button type="button" data-role="move-question" data-index="${i}" data-direction="-1" ${i===0?'disabled':''} title="Move up">
          <i class="iconoir-nav-arrow-up"></i>
        </button>
        <button type="button" data-role="move-question" data-index="${i}" data-direction="1" ${i===questions.length-1?'disabled':''} title="Move down">
          <i class="iconoir-nav-arrow-down"></i>
        </button>
        <button type="button" class="del" data-role="remove-question" data-index="${i}" title="Remove">
          <i class="iconoir-trash"></i>
        </button>
      </div>`;
    list.appendChild(item);
  });

  $('q-count').textContent = questions.length ? `(${questions.length})` : '';
}

function rebuildPreview() {
  const preview   = $('study-preview');
  preview.replaceChildren();
  const questions = state.config.questions || [];

  questions.forEach((q, i) => {
    const cardModule = CARDS[q.type];
    if (!cardModule) return;

    const wrap = document.createElement('div');
    wrap.className = 'preview-card-wrap' + (i === state.selectedIndex ? ' selected' : '');
    wrap.id = `pc-${i}`;
    // Cards in the preview are display-only — pointer-events are disabled via CSS
    wrap.innerHTML = `
      <div class="q-card-study">${cardModule.renderStudy(q, i)}</div>
      <div class="preview-card-overlay">
        <button type="button" data-role="select-card" data-index="${i}">
          <i class="iconoir-edit-pencil"></i> Edit
        </button>
      </div>`;
    preview.appendChild(wrap);
  });
}

function syncEmptyState() {
  const has = (state.config.questions || []).length > 0;
  $('preview-empty').hidden = has;
}

// ── Selection & Overlay editor ───────────────────────────────

function selectQuestion(index) {
  state.selectedIndex = index;

  document.querySelectorAll('.admin-q-item').forEach((el, i) => el.classList.toggle('selected', i === index));
  document.querySelectorAll('.preview-card-wrap').forEach((el, i) => el.classList.toggle('selected', i === index));

  openOverlay(index);

  // Scroll the preview to bring the selected card into view
  $(`pc-${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Slide the editor overlay open and populate it with the question's editor fields
function openOverlay(index) {
  const q          = state.config.questions[index];
  const cardModule = CARDS[q.type];
  if (!cardModule) return;

  const meta = getMeta(q.type);

  $('overlay-type-tag').innerHTML =
    `<i class="iconoir-${meta.icon}"></i> ${meta.label} <span class="editor-index">#${index + 1}</span>`;

  $('editor-fields').innerHTML = cardModule.renderEditor(q, index);

  // Adding the class triggers the CSS slide-in transition
  $('admin-sidebar').classList.add('has-overlay');
}

// Slide the editor overlay closed and return to the list view
function closeOverlay() {
  $('admin-sidebar').classList.remove('has-overlay');
}

function liveUpdate(index) {
  const q          = state.config.questions[index];
  const cardModule = CARDS[q.type];
  if (!cardModule) return;

  const updated = cardModule.collectConfig($('editor-fields'));
  if (!updated) return;

  state.config.questions[index] = updated;

  // Refresh the preview card in real time so the study lead sees the effect immediately
  const wrap = $(`pc-${index}`);
  if (wrap) {
    wrap.querySelector('.q-card-study').innerHTML = cardModule.renderStudy(updated, index);
  }

  // Keep the question label in the sidebar list in sync with the prompt text
  const label = $('admin-q-list').querySelector(`.admin-q-item[data-index="${index}"] .admin-q-label`);
  if (label) {
    label.innerHTML = updated.prompt ? escapeHtml(updated.prompt) : '<em>no text</em>';
  }
}

// ── Question management ──────────────────────────────────────

function addQuestion(type) {
  state.config.questions = state.config.questions || [];
  state.config.questions.push(defaultFor(type));
  const newIndex = state.config.questions.length - 1;
  rebuildAll();
  selectQuestion(newIndex);
  requestAnimationFrame(() => $(`pc-${newIndex}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
  markUnsaved();
}

function removeQuestion(index) {
  if (!confirm(`Remove question ${index + 1}?`)) return;
  state.config.questions.splice(index, 1);

  if (state.selectedIndex === index) {
    state.selectedIndex = null;
    closeOverlay();
  } else if (state.selectedIndex > index) {
    state.selectedIndex -= 1;
  }

  rebuildAll();
  if (state.selectedIndex !== null) selectQuestion(state.selectedIndex);
  markUnsaved();
}

function moveQuestion(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= state.config.questions.length) return;

  [state.config.questions[index], state.config.questions[target]] = [
    state.config.questions[target], state.config.questions[index],
  ];

  if (state.selectedIndex === index) state.selectedIndex = target;
  else if (state.selectedIndex === target) state.selectedIndex = index;

  rebuildAll();
  if (state.selectedIndex !== null) selectQuestion(state.selectedIndex);
  markUnsaved();
}


// ── Save ─────────────────────────────────────────────────────

function handleTriggerTypePill(pillElement) {
  const triggerType  = pillElement.dataset.triggerType;
  const editorFields = $('editor-fields');

  editorFields.querySelectorAll('.trigger-pill').forEach(pill => {
    pill.classList.toggle('active', pill.dataset.triggerType === triggerType);
  });

  const hiddenInput = editorFields.querySelector('.se-trigger-type');
  if (hiddenInput) hiddenInput.value = triggerType;

  const contentField = editorFields.querySelector('.se-trigger-content-field');
  if (contentField) {
    contentField.hidden = (triggerType === 'timer');

    // 'html' and 'js' need a code textarea; all other types need a URL input.
    // When switching between these two categories, replace the DOM element so the
    // field type matches the selected trigger — preserving any value already typed.
    const isCode        = triggerType === 'html' || triggerType === 'js';
    const currentInput  = contentField.querySelector('.se-trigger-content');
    const currentIsCode = currentInput?.tagName === 'TEXTAREA';

    if (currentInput && isCode !== currentIsCode) {
      const savedValue = currentInput.value;
      const labelEl    = contentField.querySelector('label');
      if (labelEl) labelEl.textContent = isCode ? 'Code' : 'URL';

      let replacement;
      if (isCode) {
        replacement             = document.createElement('textarea');
        replacement.className   = 'se-trigger-content se-trigger-content--code';
        replacement.rows        = 6;
        replacement.placeholder = `Paste ${triggerType} code here...`;
        replacement.value       = savedValue;
      } else {
        replacement             = document.createElement('input');
        replacement.type        = 'url';
        replacement.className   = 'se-trigger-content';
        replacement.placeholder = 'https://...';
        replacement.value       = savedValue;
      }
      currentInput.replaceWith(replacement);
    }
  }

  // Dispatch input event so liveUpdate and markUnsaved fire via the existing listener
  editorFields.dispatchEvent(new Event('input', { bubbles: true }));
}

async function saveConfig() {
  const fullConfig = {
    study_id:  $('cfg-id').value.trim(),
    questions: state.config.questions || [],
  };
  await postJson('/api/config', fullConfig);
  state.config = fullConfig;

  const toast = $('toast');
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2000);
  setSavedStatus('Saved ✓');
}

function markUnsaved() {
  if (!state.loaded) return;
  $('save-status').textContent = 'Unsaved changes';
  $('save-status').className   = 'save-status';
}

function setSavedStatus(label) {
  $('save-status').textContent = label;
  $('save-status').className   = 'save-status saved';
}

// ── Helpers ──────────────────────────────────────────────────

function getMeta(type) {
  const entry = CARD_TYPES.find(t => t.type === type);
  return entry
    ? (entry.overrideMeta || entry.module.meta)
    : { icon: 'question-mark', label: type };
}

init();
