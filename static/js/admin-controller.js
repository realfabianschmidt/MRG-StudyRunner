import { getJson, postJson } from './api-client.js';
import { initializeAdminDashboard } from './admin-dashboard-controller.js';
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
  initializeAdminDashboard({ showToast });

  try {
    state.config = await getJson('/api/config');
    ensureBookends(state.config.questions);
    $('cfg-id').value = state.config.study_id || '';
    updateHubTitle();
    rebuildAll();
    await loadRecentStudies();
    state.loaded = true;
    showToast('Loaded', 'info');
  } catch (error) {
    console.error('[admin] Could not load configuration:', error);
    showToast('Could not load config', 'error');
  }
}

function updateHubTitle() {
  const studyId = $('cfg-id').value.trim() || 'Unbenannte Studie';
  const hubTitle = $('hub-active-title');
  if (hubTitle) hubTitle.textContent = studyId;
}

function switchView(viewId) {
  document.querySelectorAll('.admin-view').forEach(el => {
    el.hidden = el.id !== viewId;
    el.classList.toggle('active', el.id === viewId);
  });
}

function startNewStudy() {
  state.config = { study_id: "Neue Studie", questions: [defaultFor('participant-id'), defaultFor('finish')] };
  $('cfg-id').value = "Neue Studie";
  updateHubTitle();
  rebuildAll();
  markUnsaved();
  showToast('Neue Studie erstellt. Bitte speichern!', 'info');
  
  // Direkt in den Editor springen
  $('admin-edit-view').hidden = false;
  $('admin-dashboard').hidden = true;
  switchView('view-workspace');
}

function openTypePicker() {
  $('overlay-type-tag').innerHTML = `<i class="iconoir-plus"></i> Add question`;

  $('editor-fields').innerHTML = `
    <div class="type-picker-title">Choose question type</div>
    <div class="type-grid">
      ${CARD_TYPES.filter(ct => ct.type !== 'participant-id' && ct.type !== 'finish').map(({ type, module, overrideMeta }) => {
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

  $('btn-hub-new').addEventListener('click', startNewStudy);
  $('btn-hub-editor').addEventListener('click', () => { $('admin-edit-view').hidden = false; $('admin-dashboard').hidden = true; switchView('view-workspace'); });
  $('btn-admin-dashboard').addEventListener('click', () => switchView('view-workspace'));
  $('btn-workspace-home').addEventListener('click', () => switchView('view-hub'));
  $('btn-admin-edit-view').addEventListener('click', () => switchView('view-hub'));
  $('btn-hub-settings').addEventListener('click', () => { $('admin-settings-modal').hidden = false; });

  $('cfg-id').addEventListener('input', () => { markUnsaved(); updateHubTitle(); });

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

  $('btn-close-notion-settings').addEventListener('click', closeNotionSettings);
  $('btn-notion-cancel').addEventListener('click', closeNotionSettings);
  $('btn-notion-save').addEventListener('click', () => void saveNotionSettings());
  $('btn-notion-flush').addEventListener('click', () => void flushNotionQueue());
  $('btn-notion-test').addEventListener('click', () => void testNotionConnection());
  $('btn-notion-help').addEventListener('click', openNotionHelp);
  $('btn-close-notion-help').addEventListener('click', closeNotionHelp);
  $('btn-close-notion-help-ok').addEventListener('click', closeNotionHelp);
  
  $('btn-notion-settings').addEventListener('click', () => void openNotionSettings());
  $('notion-settings-modal').addEventListener('click', (event) => {
    if (event.target === $('notion-settings-modal')) closeNotionSettings();
  });
  $('notion-help-modal').addEventListener('click', (event) => {
    if (event.target === $('notion-help-modal')) closeNotionHelp();
  });
}

function handleListClick(event) {
  if (state.suppressListClick) {
    return;
  }

  const removeButton = event.target.closest('[data-role="remove-question"]');
  const item = event.target.closest('.admin-q-item');

  if (removeButton) {
    const index = Number(removeButton.dataset.index);
    const qType = state.config.questions[index]?.type;
    if (qType === 'participant-id' || qType === 'finish') {
      showToast('Start- und End-Karten können nicht entfernt werden.', 'error');
      return;
    }
    removeQuestion(index);
    return;
  }
  if (item && !event.target.closest('.admin-q-actions')) {
    selectQuestion(Number(item.dataset.index));
  }
}

function handleListDragStart(event) {
  const handle = event.target.closest('[data-role="drag-question"]');
  if (!handle || handle.disabled) {
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

  const list = $('admin-q-list');
  const placement = getDragPlacement(list, event.clientY);
  clearDragIndicators();

  // Boundary checks to keep items between the first and last card
  const questions = state.config.questions || [];
  const firstItem = list.querySelector('.admin-q-item[data-index="0"]');
  const lastItem = list.querySelector(`.admin-q-item[data-index="${questions.length - 1}"]`);

  // Block dropping before the first item
  if (placement.targetItem === firstItem && !placement.insertAfter) {
    return;
  }
  // Block dropping after the last item
  if ((placement.targetItem === lastItem && placement.insertAfter) || !placement.targetItem) {
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
  const isFixed = question.type === 'participant-id' || question.type === 'finish';
  return `
    <span class="admin-q-num">${questionIndex + 1}</span>
    <i class="iconoir-${meta.icon} admin-q-type-icon"></i>
    <span class="admin-q-label">${renderCardLabel(question)}</span>
    <div class="admin-q-actions">
      <button type="button" class="admin-q-drag" data-role="drag-question" draggable="${!isFixed}" ${isFixed ? 'disabled' : ''} title="Drag to reorder" aria-label="Drag to reorder">
        <i class="iconoir-menu-scale"></i>
      </button>
      <button type="button" class="del" data-role="remove-question" data-index="${questionIndex}" title="Remove" ${isFixed ? 'disabled' : ''}>
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

  const editorEl = $('editor-fields');
  editorEl.innerHTML = cardModule.renderEditor(question, index);
  if (typeof cardModule.bindEditorEvents === 'function') {
    cardModule.bindEditorEvents(editorEl);
  }
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
  const questions = state.config.questions;
  const finishCardIndex = questions.findIndex(q => q.type === 'finish');
  const insertIndex = finishCardIndex !== -1 ? finishCardIndex : questions.length;

  questions.splice(insertIndex, 0, defaultFor(type));
  rebuildAll();
  selectQuestion(insertIndex);
  requestAnimationFrame(() => $(`pc-${insertIndex}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
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

function ensureBookends(questions) {
  if (!Array.isArray(questions)) return;
  const pidIndex = questions.findIndex(q => q.type === 'participant-id');
  const pidCard = pidIndex !== -1 ? questions.splice(pidIndex, 1)[0] : defaultFor('participant-id');
  const finIndex = questions.findIndex(q => q.type === 'finish');
  const finCard = finIndex !== -1 ? questions.splice(finIndex, 1)[0] : defaultFor('finish');
  questions.unshift(pidCard);
  questions.push(finCard);
}

async function saveConfig() {
  let questions = state.config.questions || [];
  ensureBookends(questions);

  const fullConfig = {
    study_id: $('cfg-id').value.trim(),
    questions: questions,
  };

  try {
    await postJson('/api/config', fullConfig);
    state.config = fullConfig;

    $('btn-save-config').classList.remove('btn-primary--dirty');
    await loadRecentStudies();
    rebuildAll();
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
      ensureBookends(config.questions);
      state.config = config;
      $('cfg-id').value = config.study_id || '';
      updateHubTitle();
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

async function loadRecentStudies() {
  try {
    const studies = await getJson('/api/admin/studies');
    const listEl = $('hub-recent-list');
    if (!studies || studies.length === 0) {
      listEl.innerHTML = `
        <div class="hub-recent-item empty">
          <i class="iconoir-clock"></i>
          <div>Noch keine Studien gespeichert.</div>
        </div>`;
      return;
    }

    listEl.innerHTML = studies.map(s => `
      <div class="hub-recent-item" data-study-id="${escapeHtml(s.id)}" style="cursor:pointer; justify-content: flex-start;">
        <i class="iconoir-journal-page" style="font-size: 20px; color: var(--accent);"></i>
        <div style="flex: 1; text-align: left;">
          <div style="font-weight: 600; color: var(--ink);">${escapeHtml(s.id)}</div>
          <div style="font-size: 0.75rem; color: var(--ink-40);">Zuletzt bearbeitet: ${new Date(s.modified * 1000).toLocaleString()}</div>
        </div>
        <i class="iconoir-nav-arrow-right"></i>
      </div>
    `).join('');

    listEl.querySelectorAll('.hub-recent-item:not(.empty)').forEach(item => {
      item.addEventListener('click', async () => {
        try {
          const config = await postJson('/api/admin/studies/active', { id: item.dataset.studyId });
          ensureBookends(config.questions);
          state.config = config;
          $('cfg-id').value = config.study_id || '';
          updateHubTitle();
          rebuildAll();
          state.loaded = true;
          showToast('Studie geladen', 'success');
          $('admin-edit-view').hidden = false;
          $('admin-dashboard').hidden = true;
          switchView('view-workspace');
        } catch (e) {
          showToast('Fehler beim Laden', 'error');
        }
      });
    });
  } catch (error) {
    console.error('[admin] Could not load recent studies:', error);
  }
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

// ── Notion Settings ───────────────────────────────────────────────────────────

async function openNotionSettings() {
  try {
    const hw = await getJson('/api/hardware-config');
    const cfg = hw.notion || {};
    $('notion-enabled').checked = Boolean(cfg.enabled);
    $('notion-api-key').value = cfg.api_key || '';
    $('notion-parent-page-id').value = cfg.parent_page_id || '';
    $('notion-database-id').value = cfg.database_id || '';
    $('notion-auto-retry').checked = cfg.auto_retry_failed !== false;
  } catch {
    showToast('Could not load Notion config', 'error');
  }
  await _refreshNotionQueueStatus();
  $('notion-settings-modal').hidden = false;
}

function closeNotionSettings() {
  $('notion-settings-modal').hidden = true;
}

async function saveNotionSettings() {
  try {
    const hw = await getJson('/api/hardware-config');
    hw.notion = {
      enabled: $('notion-enabled').checked,
      api_key: $('notion-api-key').value.trim(),
      parent_page_id: $('notion-parent-page-id').value.trim(),
      database_id: $('notion-database-id').value.trim(),
      auto_create_database: true,
      auto_retry_failed: $('notion-auto-retry').checked,
      timeout_seconds: 10,
    };
    await postJson('/api/hardware-config', hw);
    showToast('Notion config saved — restart server', 'success');
    closeNotionSettings();
  } catch (error) {
    showToast('Save failed', 'error');
    console.error('[admin] Notion save failed:', error);
  }
}

async function flushNotionQueue() {
  const btn = $('btn-notion-flush');
  const originalText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="iconoir-refresh"></i> Uploading...';

  try {
    const result = await postJson('/api/notion/flush-queue', {});
    await _refreshNotionQueueStatus();

    const err = result.last_error || result.error;
    if (result.remaining > 0 && err) {
      alert(`Fehler beim Hochladen!\n\nEs konnten ${result.remaining} Einträge nicht hochgeladen werden.\n\nGrund:\n${err}`);
      showToast(`${result.remaining} fehlgeschlagen`, 'error');
    } else {
      showToast(`${result.succeeded ?? 0} erfolgreich hochgeladen!`, 'success');
    }
  } catch {
    showToast('Flush failed', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
}

async function testNotionConnection() {
  const btn = $('btn-notion-test');
  const resultEl = $('notion-test-result');
  const icon = $('notion-test-icon');

  btn.disabled = true;
  icon.className = 'iconoir-refresh';
  resultEl.hidden = true;
  resultEl.innerHTML = '';

  try {
    const result = await postJson('/api/notion/test', {
      api_key: $('notion-api-key').value.trim(),
      parent_page_id: $('notion-parent-page-id').value.trim(),
      database_id: $('notion-database-id').value.trim(),
    });

    const checks = result.checks || [];
    const rows = checks.map(c => {
      const statusIcon = c.ok === true
        ? '<i class="iconoir-check-circle" style="color:var(--accent-green,#22c55e)"></i>'
        : c.ok === false
          ? '<i class="iconoir-xmark-circle" style="color:var(--accent-red,#ef4444)"></i>'
          : '<i class="iconoir-info-circle" style="color:var(--ink-40,#999)"></i>';
      return `<div class="notion-test-row">${statusIcon}<span><strong>${c.name}</strong> — ${c.message}</span></div>`;
    }).join('');

    resultEl.innerHTML = `<div class="notion-test-result-box ${result.ok ? 'notion-test-result-box--ok' : 'notion-test-result-box--fail'}">${rows}</div>`;
    resultEl.hidden = false;
    icon.className = result.ok ? 'iconoir-plug' : 'iconoir-plug-xmark';
  } catch (error) {
    resultEl.innerHTML = `<div class="notion-test-result-box notion-test-result-box--fail"><div class="notion-test-row"><i class="iconoir-xmark-circle" style="color:var(--accent-red,#ef4444)"></i><span>Server-Fehler: ${error.message}</span></div></div>`;
    resultEl.hidden = false;
    icon.className = 'iconoir-plug-xmark';
  } finally {
    btn.disabled = false;
  }
}

function openNotionHelp() {
  $('notion-help-modal').hidden = false;
}

function closeNotionHelp() {
  $('notion-help-modal').hidden = true;
}

async function _refreshNotionQueueStatus() {
  try {
    const status = await getJson('/api/notion/status');
    const el = $('notion-queue-status');
    if (el) {
      const qSize = status.queue_size ?? 0;
      const connected = status.connected ? 'verbunden' : 'getrennt';
      el.textContent = `Queue: ${qSize} ausstehend · API: ${connected}`;
    }
  } catch {
    // status endpoint unavailable — ignore
  }
}

void init();
