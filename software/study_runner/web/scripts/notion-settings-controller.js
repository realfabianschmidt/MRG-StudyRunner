import { getJson, postJson } from './api-client.js';

let callbacks = {};
let clearKeyRequested = false;
let initialized = false;

const $ = (id) => document.getElementById(id);

function defaultStudySettings() {
  return {
    sensors_enabled: true,
    notion_enabled: false,
    notion_parent_page_id: '',
    notion_database_id: '',
    notion_data_source_id: '',
  };
}

function normalizeStudySettings(settings) {
  return {
    ...defaultStudySettings(),
    ...(settings && typeof settings === 'object' ? settings : {}),
    sensors_enabled: settings?.sensors_enabled !== false,
    notion_enabled: Boolean(settings?.notion_enabled),
    notion_parent_page_id: String(settings?.notion_parent_page_id || '').trim(),
    notion_database_id: String(settings?.notion_database_id || '').trim(),
    notion_data_source_id: String(settings?.notion_data_source_id || '').trim(),
  };
}

export function initializeNotionSettings(options = {}) {
  callbacks = options;
  if (initialized) {
    return;
  }
  initialized = true;

  $('btn-notion-settings')?.addEventListener('click', () => void openNotionSettings());
  $('btn-notion-back')?.addEventListener('click', () => callbacks.switchView?.('view-hub'));
  $('btn-notion-save')?.addEventListener('click', () => void saveGlobalNotionSettings());
  $('btn-notion-study-save')?.addEventListener('click', () => void saveStudyNotionSettings());
  $('btn-notion-flush')?.addEventListener('click', () => void flushNotionQueue());
  $('btn-notion-test')?.addEventListener('click', () => void testNotionConnection());
  $('btn-notion-clear-key')?.addEventListener('click', () => setClearKeyRequested(!clearKeyRequested));
  $('notion-study-enabled')?.addEventListener('change', toggleStudyFields);

  setClearKeyRequested(false);
}

async function openNotionSettings() {
  callbacks.switchView?.('view-notion-settings');
  try {
    const hardwareConfig = await getJson('/api/hardware-config');
    const config = hardwareConfig.notion || {};
    $('notion-enabled').checked = Boolean(config.enabled);
    $('notion-api-key').value = '';
    $('notion-auto-retry').checked = config.auto_retry_failed !== false;
    setClearKeyRequested(false);
    populateStudyForm();
    $('notion-api-key-status').textContent = config.api_key_configured
      ? 'API key is already stored on this backend. Leave the field empty to keep it.'
      : 'No backend-local API key is stored yet.';
  } catch (error) {
    console.error('[notion] Could not load config:', error);
    callbacks.showToast?.('Could not load Notion settings', 'error');
  }
  await refreshNotionStatus();
}

async function saveGlobalNotionSettings() {
  try {
    const hardwareConfig = await getJson('/api/hardware-config');
    hardwareConfig.notion = {
      ...(hardwareConfig.notion || {}),
      enabled: $('notion-enabled').checked,
      api_key: $('notion-api-key').value.trim(),
      auto_retry_failed: $('notion-auto-retry').checked,
      timeout_seconds: 10,
      clear_api_key: clearKeyRequested,
    };
    await postJson('/api/hardware-config', hardwareConfig);
    $('notion-api-key').value = '';
    setClearKeyRequested(false);
    callbacks.showToast?.('Notion settings saved', 'success');
    await refreshNotionStatus();
  } catch (error) {
    console.error('[notion] Save failed:', error);
    callbacks.showToast?.('Notion save failed', 'error');
  }
}

async function saveStudyNotionSettings() {
  const currentConfig = callbacks.getStudyConfig?.() || {};
  const currentSettings = normalizeStudySettings(currentConfig.study_settings);
  callbacks.setStudySettings?.({
    ...currentSettings,
    notion_enabled: $('notion-study-enabled').checked,
    notion_parent_page_id: $('notion-study-parent-id').value.trim(),
    notion_database_id: $('notion-study-database-id').value.trim(),
  });

  const saved = await callbacks.saveStudyConfig?.({
    successMessage: 'Studie inklusive Notion-Settings gespeichert.',
  });
  if (saved !== false) {
    callbacks.showToast?.('Study Notion target saved', 'success');
    await refreshNotionStatus();
  }
}

async function flushNotionQueue() {
  const button = $('btn-notion-flush');
  const originalText = button?.innerHTML || '';
  if (button) {
    button.disabled = true;
    button.innerHTML = '<i class="iconoir-refresh"></i> Uploading...';
  }

  try {
    const result = await postJson('/api/notion/flush-queue', {});
    await refreshNotionStatus();
    const err = result.last_error || result.error;
    if (result.remaining > 0 && err) {
      callbacks.showToast?.(`${result.remaining} uploads failed`, 'error');
    } else {
      callbacks.showToast?.(`${result.succeeded ?? 0} uploads completed`, 'success');
    }
  } catch (error) {
    console.error('[notion] Flush failed:', error);
    callbacks.showToast?.('Notion queue upload failed', 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = originalText;
    }
  }
}

async function testNotionConnection() {
  const button = $('btn-notion-test');
  const resultEl = $('notion-test-result');
  const icon = $('notion-test-icon');
  if (button) button.disabled = true;
  if (icon) icon.className = 'iconoir-refresh';
  if (resultEl) {
    resultEl.hidden = true;
    resultEl.innerHTML = '';
  }

  try {
    const result = await postJson('/api/notion/test', {
      api_key: $('notion-api-key').value.trim(),
    });
    renderTestResult(result);
    if (icon) icon.className = result.ok ? 'iconoir-plug' : 'iconoir-plug-xmark';
  } catch (error) {
    if (resultEl) {
      resultEl.innerHTML = `<div class="notion-test-result-box notion-test-result-box--fail"><div class="notion-test-row"><i class="iconoir-xmark-circle"></i><span>Server error: ${escapeHtml(error.message)}</span></div></div>`;
      resultEl.hidden = false;
    }
    if (icon) icon.className = 'iconoir-plug-xmark';
  } finally {
    if (button) button.disabled = false;
  }
}

function renderTestResult(result) {
  const resultEl = $('notion-test-result');
  if (!resultEl) {
    return;
  }
  const checks = result.checks || [];
  const rows = checks.map((check) => {
    const iconClass = check.ok === true ? 'iconoir-check-circle' : check.ok === false ? 'iconoir-xmark-circle' : 'iconoir-info-circle';
    return `<div class="notion-test-row"><i class="${iconClass}"></i><span><strong>${escapeHtml(check.name)}</strong> - ${escapeHtml(check.message)}</span></div>`;
  }).join('');
  resultEl.innerHTML = `<div class="notion-test-result-box ${result.ok ? 'notion-test-result-box--ok' : 'notion-test-result-box--fail'}">${rows}</div>`;
  resultEl.hidden = false;
}

async function refreshNotionStatus() {
  try {
    const status = await getJson('/api/notion/status');
    renderNotionStatus(status);
    return status;
  } catch (error) {
    console.error('[notion] Could not refresh status:', error);
    return null;
  }
}

function renderNotionStatus(status) {
  if (!status) {
    return;
  }
  const studyName = status.current_study_id || getCurrentStudyName();
  setText('notion-active-study-name', studyName);
  setText('notion-global-status', globalStatusLabel(status));
  setText('notion-global-hint', globalStatusHint(status));
  setText('notion-storage-value', status.api_key_configured ? (status.api_key_storage || 'configured') : 'not stored');
  setText('notion-storage-hint', status.api_key_configured
    ? 'New API-key input is stored backend-local on this computer.'
    : `Saving stores the key in ${status.local_secrets_file || 'local_secrets.json'}.`);
  setText('notion-api-key-status', status.api_key_configured
    ? `Currently used: ${status.api_key_storage || 'configured'}. Leave empty to keep it.`
    : 'No backend-local API key is stored yet.');
  setText('notion-study-status', status.current_study_notion_enabled ? `${studyName} uploads` : `${studyName} does not upload`);
  setText('notion-study-hint', studyStatusHint(status));
  const queueSize = status.queue_size ?? 0;
  setText('notion-queue-value', queueSize > 0 ? `${queueSize} waiting` : 'empty');
  setText('notion-queue-detail', queueExplanation(status));
  setText('notion-queue-status', `Queue: ${queueSize} waiting - API: ${status.connected ? 'connected' : 'not connected'}`);
}

function globalStatusLabel(status) {
  if (!status.enabled_globally) return 'disabled';
  if (status.connected) return 'connected';
  if (status.api_key_configured) return 'configured';
  return 'waiting for API key';
}

function globalStatusHint(status) {
  if (!status.enabled_globally) return 'Global upload is disabled.';
  if (!status.api_key_configured) return 'No effective API key is stored on the backend.';
  if (!status.connected) return 'API key exists, but the adapter is not connected yet.';
  return 'The Notion adapter is ready on this computer.';
}

function studyStatusHint(status) {
  if (!status.current_study_notion_enabled) return 'This study will not upload completed sessions.';
  if (status.current_study_database_id) return 'A Notion database is configured for this study.';
  if (status.current_study_parent_page_id) return 'Parent page is set; the database can be created automatically.';
  return 'This study still needs a Parent Page ID or Database ID.';
}

function queueExplanation(status) {
  const queueSize = status.queue_size ?? 0;
  if (queueSize > 0) return 'Failed uploads are waiting for retry.';
  if (!status.current_study_notion_enabled) return 'Queue is empty because the active study has Notion upload disabled.';
  if (!status.enabled_globally) return 'Queue is empty because the global Notion integration is disabled.';
  if (!status.api_key_configured) return 'Queue is empty because no API key is stored.';
  if (!status.current_study_target_ready) return 'Queue is empty. The active study still needs a Notion target.';
  if (!status.connected) return 'Queue is empty. The adapter is not connected yet, but failed uploads will be buffered.';
  return 'Queue is empty. New completed sessions should upload directly.';
}

function populateStudyForm() {
  const config = callbacks.getStudyConfig?.() || {};
  const settings = normalizeStudySettings(config.study_settings);
  setText('notion-active-study-name', getCurrentStudyName());
  $('notion-study-enabled').checked = Boolean(settings.notion_enabled);
  $('notion-study-parent-id').value = settings.notion_parent_page_id || '';
  $('notion-study-database-id').value = settings.notion_database_id || '';
  toggleStudyFields();
}

function toggleStudyFields() {
  const fields = $('notion-study-fields');
  if (fields) {
    fields.hidden = !$('notion-study-enabled').checked;
  }
}

function setClearKeyRequested(value) {
  clearKeyRequested = Boolean(value);
  const hidden = $('notion-clear-api-key');
  if (hidden) hidden.value = clearKeyRequested ? '1' : '0';
  const button = $('btn-notion-clear-key');
  if (button) {
    button.innerHTML = clearKeyRequested
      ? '<i class="iconoir-check"></i> Loeschung vorgemerkt'
      : '<i class="iconoir-trash"></i> Backend-Key loeschen';
  }
  setText('notion-clear-key-state', clearKeyRequested
    ? 'Der gespeicherte backend-lokale Key wird beim naechsten Speichern entfernt.'
    : 'Kein Loeschvorgang vorgemerkt.');
}

function getCurrentStudyName() {
  return callbacks.getCurrentStudyName?.() || 'Unbenannte Studie';
}

function setText(id, value) {
  const target = $(id);
  if (target) target.textContent = value;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
