import { getJson, postJson } from './api-client.js';
import { CARDS, CARD_TYPES, defaultFor } from './cards/index.js';

function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');
}

const state = {
  config: {},
  selectedIndex: null,
  loaded: false,
  draggedElement: null,
  suppressListClick: false,
};

const $ = (id) => document.getElementById(id);

async function init() {
  bindEvents();

  try {
    state.config = await getJson('/api/config');
    $('cfg-id').value = state.config.study_id || '';
    rebuildAll();
    state.loaded = true;
    showToast('Loaded', 'info');
  } catch (error) {
    console.error('[admin] Could not load configuration:', error);
    showToast('Could not load config', 'error');
  }
}

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

function bindEvents() {
  $('btn-add-main').addEventListener('click', openTypePicker);
  $('btn-save-config').addEventListener('click', () => void saveConfig());
  $('btn-load-config').addEventListener('click', loadFromFile);
  $('overlay-close').addEventListener('click', closeOverlay);

  $('sidebar-overlay').addEventListener('click', (event) => {
    const typeButton = event.target.closest('[data-add-type]');
    const triggerPill = event.target.closest('[data-trigger-type]');
    if (typeButton) {
      addQuestion(typeButton.dataset.addType);
      return;
    }
    if (triggerPill) {
      handleTriggerTypePill(triggerPill);
    }
  });

  const questionList = $('admin-q-list');
  questionList.addEventListener('click', handleListClick);
  questionList.addEventListener('dragstart', handleListDragStart);
  questionList.addEventListener('dragover', handleListDragOver);
  questionList.addEventListener('drop', handleListDrop);
  questionList.addEventListener('dragend', handleListDragEnd);

  $('study-preview').addEventListener('click', (event) => {
    const button = event.target.closest('[data-role="select-card"]');
    if (button) {
      selectQuestion(Number(button.dataset.index));
    }
  });

  $('sidebar-overlay').addEventListener('input', () => {
    if (state.selectedIndex !== null) {
      liveUpdate(state.selectedIndex);
    }
    markUnsaved();
  });

  $('cfg-id').addEventListener('input', markUnsaved);
}

function handleListClick(event) {
  if (state.suppressListClick) {
    return;
  }

  const removeButton = event.target.closest('[data-role="remove-question"]');
  const item = event.target.closest('.admin-q-item');

  if (removeButton) {
    removeQuestion(Number(removeButton.dataset.index));
    return;
  }
  if (item && !event.target.closest('.admin-q-actions')) {
    selectQuestion(Number(item.dataset.index));
  }
}

function handleListDragStart(event) {
  const handle = event.target.closest('[data-role="drag-question"]');
  if (!handle) {
    event.preventDefault();
    return;
  }

  const item = handle.closest('.admin-q-item');
  if (!item) {
    event.preventDefault();
    return;
  }

  state.draggedElement = item;
  $('admin-q-list').classList.add('admin-q-list--dragging');

  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.dropEffect = 'move';
    event.dataTransfer.setData('text/plain', item.dataset.index || '');
  }

  window.requestAnimationFrame(() => {
    item.classList.add('admin-q-item--dragging');
  });
}

function handleListDragOver(event) {
  if (!state.draggedElement) {
    return;
  }

  event.preventDefault();

  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'move';
  }

  const list = $('admin-q-list');
  const placement = getDragPlacement(list, event.clientY);
  clearDragIndicators();

  if (!placement.targetItem) {
    list.appendChild(state.draggedElement);
    return;
  }

  if (placement.targetItem !== state.draggedElement) {
    placement.targetItem.classList.add(
      placement.insertAfter ? 'admin-q-item--drop-after' : 'admin-q-item--drop-before',
    );
  }

  const referenceNode = placement.insertAfter
    ? placement.targetItem.nextElementSibling
    : placement.targetItem;

  if (referenceNode !== state.draggedElement) {
    list.insertBefore(state.draggedElement, referenceNode);
  }
}

function handleListDrop(event) {
  if (!state.draggedElement) {
    return;
  }
  event.preventDefault();
}

function handleListDragEnd() {
  finishListDrag();
}

function getDragPlacement(list, clientY) {
  const items = [...list.querySelectorAll('.admin-q-item:not(.admin-q-item--dragging)')];

  for (const item of items) {
    const rect = item.getBoundingClientRect();
    const midpoint = rect.top + (rect.height / 2);

    if (clientY < midpoint) {
      return { targetItem: item, insertAfter: false };
    }
    if (clientY < rect.bottom) {
      return { targetItem: item, insertAfter: true };
    }
  }

  return { targetItem: null, insertAfter: false };
}

function finishListDrag() {
  const list = $('admin-q-list');
  const draggedElement = state.draggedElement;
  if (!draggedElement) {
    return;
  }

  const previousSelection = state.selectedIndex;
  const shouldKeepOverlayOpen = $('admin-sidebar').classList.contains('has-overlay');
  const previousQuestions = [...(state.config.questions || [])];
  const orderedIndexes = [...list.querySelectorAll('.admin-q-item')].map((item) => Number(item.dataset.index));
  const orderChanged = orderedIndexes.some((originalIndex, newIndex) => originalIndex !== newIndex);

  clearDragIndicators();
  list.classList.remove('admin-q-list--dragging');
  draggedElement.classList.remove('admin-q-item--dragging');
  state.draggedElement = null;
  suppressListClickOnce();

  if (!orderChanged) {
    return;
  }

  state.config.questions = orderedIndexes.map((index) => previousQuestions[index]);
  state.selectedIndex = previousSelection === null ? null : orderedIndexes.indexOf(previousSelection);

  rebuildAll();

  if (shouldKeepOverlayOpen && state.selectedIndex !== null) {
    openOverlay(state.selectedIndex);
    $(`pc-${state.selectedIndex}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  markUnsaved();
}

function clearDragIndicators() {
  document.querySelectorAll('.admin-q-item--drop-before, .admin-q-item--drop-after').forEach((element) => {
    element.classList.remove('admin-q-item--drop-before', 'admin-q-item--drop-after');
  });
}

function suppressListClickOnce() {
  state.suppressListClick = true;
  window.setTimeout(() => {
    state.suppressListClick = false;
  }, 0);
}

function rebuildAll() {
  rebuildList();
  rebuildPreview();
  syncEmptyState();
}

function rebuildList() {
  const list = $('admin-q-list');
  list.replaceChildren();
  const questions = state.config.questions || [];

  questions.forEach((question, questionIndex) => {
    const meta = getMeta(question.type);
    const item = document.createElement('div');
    item.className = `admin-q-item${questionIndex === state.selectedIndex ? ' selected' : ''}`;
    item.dataset.index = questionIndex;
    item.innerHTML = renderListItemMarkup(question, questionIndex, meta);
    list.appendChild(item);
  });

  $('q-count').textContent = questions.length ? `(${questions.length})` : '';
}

function renderListItemMarkup(question, questionIndex, meta) {
  return `
    <span class="admin-q-num">${questionIndex + 1}</span>
    <i class="iconoir-${meta.icon} admin-q-type-icon"></i>
    <span class="admin-q-label">${renderCardLabel(question)}</span>
    <div class="admin-q-actions">
      <button type="button" class="admin-q-drag" data-role="drag-question" draggable="true" title="Drag to reorder" aria-label="Drag to reorder">
        <i class="iconoir-menu-scale"></i>
      </button>
      <button type="button" class="del" data-role="remove-question" data-index="${questionIndex}" title="Remove">
        <i class="iconoir-trash"></i>
      </button>
    </div>`;
}

function rebuildPreview() {
  const preview = $('study-preview');
  preview.replaceChildren();
  const questions = state.config.questions || [];

  questions.forEach((question, questionIndex) => {
    const cardModule = CARDS[question.type];
    if (!cardModule) {
      return;
    }

    const wrap = document.createElement('div');
    wrap.className = `preview-card-wrap${questionIndex === state.selectedIndex ? ' selected' : ''}`;
    wrap.id = `pc-${questionIndex}`;
    wrap.innerHTML = `
      <div class="q-card-study">${cardModule.renderStudy(question, questionIndex)}</div>
      <div class="preview-card-overlay">
        <button type="button" data-role="select-card" data-index="${questionIndex}">
          <i class="iconoir-edit-pencil"></i> Edit
        </button>
      </div>`;
    preview.appendChild(wrap);
  });
}

function syncEmptyState() {
  $('preview-empty').hidden = (state.config.questions || []).length > 0;
}

function selectQuestion(index) {
  state.selectedIndex = index;

  document.querySelectorAll('.admin-q-item').forEach((element, elementIndex) => {
    element.classList.toggle('selected', elementIndex === index);
  });
  document.querySelectorAll('.preview-card-wrap').forEach((element, elementIndex) => {
    element.classList.toggle('selected', elementIndex === index);
  });

  openOverlay(index);
  $(`pc-${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function openOverlay(index) {
  const question = state.config.questions[index];
  const cardModule = CARDS[question.type];
  if (!cardModule) {
    return;
  }

  const meta = getMeta(question.type);
  $('overlay-type-tag').innerHTML =
    `<i class="iconoir-${meta.icon}"></i> ${meta.label} <span class="editor-index">#${index + 1}</span>`;

  $('editor-fields').innerHTML = cardModule.renderEditor(question, index);
  $('admin-sidebar').classList.add('has-overlay');
}

function closeOverlay() {
  $('admin-sidebar').classList.remove('has-overlay');
}

function liveUpdate(index) {
  const question = state.config.questions[index];
  const cardModule = CARDS[question.type];
  if (!cardModule) {
    return;
  }

  const updated = cardModule.collectConfig($('editor-fields'));
  if (!updated) {
    return;
  }

  state.config.questions[index] = updated;

  const previewWrap = $(`pc-${index}`);
  if (previewWrap) {
    previewWrap.querySelector('.q-card-study').innerHTML = cardModule.renderStudy(updated, index);
  }

  const label = $('admin-q-list').querySelector(`.admin-q-item[data-index="${index}"] .admin-q-label`);
  if (label) {
    label.innerHTML = renderCardLabel(updated);
  }
}

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
  if (!confirm(`Remove question ${index + 1}?`)) {
    return;
  }

  state.config.questions.splice(index, 1);

  if (state.selectedIndex === index) {
    state.selectedIndex = null;
    closeOverlay();
  } else if (state.selectedIndex > index) {
    state.selectedIndex -= 1;
  }

  rebuildAll();
  if (state.selectedIndex !== null) {
    selectQuestion(state.selectedIndex);
  }
  markUnsaved();
}

function handleTriggerTypePill(pillElement) {
  const triggerType = pillElement.dataset.triggerType;
  const editorFields = $('editor-fields');

  editorFields.querySelectorAll('.trigger-pill').forEach((pill) => {
    pill.classList.toggle('active', pill.dataset.triggerType === triggerType);
  });

  const hiddenInput = editorFields.querySelector('.se-trigger-type');
  if (hiddenInput) {
    hiddenInput.value = triggerType;
  }

  const contentField = editorFields.querySelector('.se-trigger-content-field');
  if (contentField) {
    contentField.hidden = triggerType === 'timer';

    const isCode = triggerType === 'html' || triggerType === 'js';
    const currentInput = contentField.querySelector('.se-trigger-content');
    const currentIsCode = currentInput?.tagName === 'TEXTAREA';

    if (currentInput && isCode !== currentIsCode) {
      const savedValue = currentInput.value;
      const label = contentField.querySelector('label');
      if (label) {
        label.textContent = isCode ? 'Code' : 'URL';
      }

      let replacement;
      if (isCode) {
        replacement = document.createElement('textarea');
        replacement.className = 'se-trigger-content se-trigger-content--code';
        replacement.rows = 6;
        replacement.placeholder = `Paste ${triggerType} code here...`;
        replacement.value = savedValue;
      } else {
        replacement = document.createElement('input');
        replacement.type = 'url';
        replacement.className = 'se-trigger-content';
        replacement.placeholder = 'https://...';
        replacement.value = savedValue;
      }
      currentInput.replaceWith(replacement);
    }
  }

  editorFields.dispatchEvent(new Event('input', { bubbles: true }));
}

async function saveConfig() {
  const fullConfig = {
    study_id: $('cfg-id').value.trim(),
    questions: state.config.questions || [],
  };

  try {
    await postJson('/api/config', fullConfig);
    state.config = fullConfig;

    $('btn-save-config').classList.remove('btn-primary--dirty');
    showToast('Saved', 'success');
  } catch (error) {
    console.error('[admin] Could not save configuration:', error);
    showToast('Save failed', 'error');
  }
}

function markUnsaved() {
  if (!state.loaded) return;
  $('btn-save-config').classList.add('btn-primary--dirty');
}

let _toastTimer = null;
function showToast(message, type = 'info') {
  const icons = { success: 'iconoir-check', error: 'iconoir-xmark-circle', info: 'iconoir-info-circle' };
  $('toast-icon').className = icons[type] || icons.info;
  $('toast-msg').textContent = message;
  const toast = $('toast');
  toast.className = `toast toast--${type} show`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove('show'), 2500);
}

function loadFromFile() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json,application/json';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const config = JSON.parse(await file.text());
      state.config = config;
      $('cfg-id').value = config.study_id || '';
      rebuildAll();
      state.loaded = true;
      markUnsaved();
      showToast(`Loaded: ${file.name}`, 'info');
    } catch {
      showToast('Invalid JSON file', 'error');
    }
  };
  input.click();
}

function renderCardLabel(question) {
  const label = getCardLabel(question);
  return label ? escapeHtml(label) : '<em>no text</em>';
}

function getCardLabel(question) {
  if (question.type === 'stimulus') {
    return (question.title || '').trim();
  }
  return (question.prompt || '').trim();
}

function getMeta(type) {
  const entry = CARD_TYPES.find((cardType) => cardType.type === type);
  return entry
    ? (entry.overrideMeta || entry.module.meta)
    : { icon: 'question-mark', label: type };
}

void init();
