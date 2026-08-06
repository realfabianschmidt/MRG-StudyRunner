import { getJson, postJson } from '../../shared/api-client.js';

import { t } from '../../shared/i18n.js';
import { escapeHtml } from '../../shared/dom-utils.js';
import { normalizeStudySettings } from '../../shared/study-settings.js';

let callbacks = {};
let clearKeyRequested = false;
let initialized = false;

const $ = (id) => document.getElementById(id);

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
  $('btn-notion-study-save-key')?.addEventListener('click', () => void saveStudyNotionKey());
  $('btn-notion-study-clear-key')?.addEventListener('click', () => void clearStudyNotionKey());

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
    void refreshStudyKeyState();
    $('notion-api-key-status').textContent = config.api_key_configured
      ? t('notion.apiKeyAlreadyStored', 'API key is already stored on this backend. Leave the field empty to keep it.')
      : t('notion.noBackendApiKey', 'No backend-local API key is stored yet.');
  } catch (error) {
    console.error('[notion] Could not load config:', error);
    callbacks.showToast?.(t('notion.loadFailed', 'Could not load Notion settings'), 'error');
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
    callbacks.showToast?.(t('notion.settingsSaved', 'Notion settings saved'), 'success');
    await refreshNotionStatus();
  } catch (error) {
    console.error('[notion] Save failed:', error);
    callbacks.showToast?.(t('notion.saveFailed', 'Notion save failed'), 'error');
  }
}

async function saveStudyNotionSettings() {
  const currentConfig = callbacks.getStudyConfig?.() || {};
  const currentSettings = normalizeStudySettings(currentConfig.study_settings);
  const plugins = { ...currentSettings.plugins };
  const previous = plugins.notion || {};
  plugins.notion = {
    enabled: $('notion-study-enabled').checked,
    required: false,
    settings: {
      ...(previous.settings || {}),
      parent_page_id: $('notion-study-parent-id').value.trim(),
      database_id: $('notion-study-database-id').value.trim(),
    },
  };
  callbacks.setStudySettings?.({
    ...currentSettings,
    plugins,
  });

  const saved = await callbacks.saveStudyConfig?.({
    successMessage: t('notion.studySettingsSavedFull', 'Study including Notion settings saved.'),
  });
  if (saved !== false) {
    callbacks.showToast?.(t('notion.studyTargetSaved', 'Study Notion target saved'), 'success');
    await refreshNotionStatus();
  }
}

async function flushNotionQueue() {
  const button = $('btn-notion-flush');
  const originalText = button?.innerHTML || '';
  if (button) {
    button.disabled = true;
    button.innerHTML = `<i class="iconoir-refresh"></i> ${escapeHtml(t('notion.uploading', 'Uploading...'))}`;
  }

  try {
    const result = await postJson('/api/notion/flush-queue', {});
    await refreshNotionStatus();
    const err = result.last_error || result.error;
    if (result.remaining > 0 && err) {
      callbacks.showToast?.(t('notion.uploadsFailed', '{count} uploads failed').replace('{count}', String(result.remaining)), 'error');
    } else {
      callbacks.showToast?.(t('notion.uploadsCompleted', '{count} uploads completed').replace('{count}', String(result.succeeded ?? 0)), 'success');
    }
  } catch (error) {
    console.error('[notion] Flush failed:', error);
    callbacks.showToast?.(t('notion.queueUploadFailed', 'Notion queue upload failed'), 'error');
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
    if (icon) icon.className = result.ok ? 'iconoir-ev-plug' : 'iconoir-ev-plug-xmark';
  } catch (error) {
    if (resultEl) {
      resultEl.innerHTML = `<div class="notion-test-result-box notion-test-result-box--fail"><div class="notion-test-row"><i class="iconoir-xmark-circle"></i><span>${escapeHtml(t('notion.serverError', 'Server error'))}: ${escapeHtml(error.message)}</span></div></div>`;
      resultEl.hidden = false;
    }
    if (icon) icon.className = 'iconoir-ev-plug-xmark';
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
  setText('notion-storage-value', status.api_key_configured ? (status.api_key_storage || t('notion.configured', 'configured')) : t('notion.notStored', 'not stored'));
  setText('notion-storage-hint', status.api_key_configured
    ? t('notion.newKeyStoredLocal', 'New API-key input is stored backend-local on this computer.')
    : t('notion.savingStoresKey', 'Saving stores the key in {file}.').replace('{file}', status.local_secrets_file || 'local_secrets.json'));
  setText('notion-api-key-status', status.api_key_configured
    ? t('notion.currentlyUsed', 'Currently used: {source}. Leave empty to keep it.').replace('{source}', status.api_key_storage || t('notion.configured', 'configured'))
    : t('notion.noBackendApiKey', 'No backend-local API key is stored yet.'));
  setText('notion-study-status', status.current_study_notion_enabled
    ? t('notion.studyUploads', '{name} uploads').replace('{name}', studyName)
    : t('notion.studyDoesNotUpload', '{name} does not upload').replace('{name}', studyName));
  setText('notion-study-hint', studyStatusHint(status));
  const queueSize = status.queue_size ?? 0;
  setText('notion-queue-value', queueSize > 0 ? t('notion.queueWaiting', '{count} waiting').replace('{count}', String(queueSize)) : t('notion.queueEmpty', 'empty'));
  setText('notion-queue-detail', queueExplanation(status));
  setText('notion-queue-status', t('notion.queueStatus', 'Queue: {count} waiting - API: {state}')
    .replace('{count}', String(queueSize))
    .replace('{state}', status.connected ? t('notion.connected', 'connected') : t('notion.notConnected', 'not connected')));
  renderStepStates(status);
}

function globalStatusLabel(status) {
  if (!status.enabled_globally) return t('notion.disabled', 'disabled');
  if (status.connected) return t('notion.connected', 'connected');
  if (status.api_key_configured) return t('notion.configured', 'configured');
  return t('notion.waitingApiKey', 'waiting for API key');
}

function globalStatusHint(status) {
  if (!status.enabled_globally) return t('notion.globalDisabledHint', 'Global upload is disabled.');
  if (!status.api_key_configured) return t('notion.noEffectiveApiKey', 'No effective API key is stored on the backend.');
  if (!status.connected) return t('notion.adapterNotConnected', 'API key exists, but the adapter is not connected yet.');
  return t('notion.adapterReady', 'The Notion adapter is ready on this computer.');
}

function studyStatusHint(status) {
  if (!status.current_study_notion_enabled) return t('notion.studyUploadDisabledHint', 'This study will not upload completed sessions.');
  if (status.current_study_database_id) return t('notion.databaseConfiguredHint', 'A Notion database is configured for this study.');
  if (status.current_study_parent_page_id) return t('notion.parentPageReadyHint', 'Parent page is set; the database can be created automatically.');
  return t('notion.studyNeedsTargetHint', 'This study still needs a Parent Page ID or Database ID.');
}

function queueExplanation(status) {
  const queueSize = status.queue_size ?? 0;
  if (queueSize > 0) return t('notion.queueFailedWaiting', 'Failed uploads are waiting for retry.');
  if (!status.current_study_notion_enabled) return t('notion.queueStudyDisabled', 'Queue is empty because the active study has Notion upload disabled.');
  if (!status.enabled_globally) return t('notion.queueGlobalDisabled', 'Queue is empty because the global Notion integration is disabled.');
  if (!status.api_key_configured) return t('notion.queueNoApiKey', 'Queue is empty because no API key is stored.');
  if (!status.current_study_target_ready) return t('notion.queueNeedsTarget', 'Queue is empty. The active study still needs a Notion target.');
  if (!status.connected) return t('notion.queueAdapterDisconnected', 'Queue is empty. The adapter is not connected yet, but failed uploads will be buffered.');
  return t('notion.queueUploadsDirect', 'Queue is empty. New completed sessions should upload directly.');
}

function renderStepStates(status) {
  setStepState('notion-step-1-state', 'optional', t('notion.stepStateExternal', 'external'));
  setStepState(
    'notion-step-2-state',
    status.api_key_configured ? 'ready' : 'missing',
    status.api_key_configured ? t('notion.stepStateReady', 'ready') : t('notion.stepStateMissing', 'missing'),
  );
  setStepState(
    'notion-step-3-state',
    status.connected ? 'ready' : 'missing',
    status.connected ? t('notion.stepStateReady', 'ready') : t('notion.stepStateMissing', 'missing'),
  );
  setStepState(
    'notion-step-4-state',
    status.current_study_target_ready ? 'ready' : 'missing',
    status.current_study_target_ready
      ? t('notion.stepStateReady', 'ready')
      : t('notion.stepStateMissing', 'missing'),
  );
}

function setStepState(id, state, label) {
  const target = $(id);
  if (!target) {
    return;
  }
  target.textContent = label;
  target.dataset.state = state;
}

function populateStudyForm() {
  const config = callbacks.getStudyConfig?.() || {};
  const settings = normalizeStudySettings(config.study_settings);
  const notion = settings.plugins?.notion || {};
  const notionSettings = notion.settings || {};
  setText('notion-active-study-name', getCurrentStudyName());
  $('notion-study-enabled').checked = Boolean(notion.enabled);
  $('notion-study-parent-id').value = notionSettings.parent_page_id || '';
  $('notion-study-database-id').value = notionSettings.database_id || '';
}

function setClearKeyRequested(value) {
  clearKeyRequested = Boolean(value);
  const hidden = $('notion-clear-api-key');
  if (hidden) hidden.value = clearKeyRequested ? '1' : '0';
  const button = $('btn-notion-clear-key');
  if (button) {
    button.innerHTML = clearKeyRequested
      ? `<i class="iconoir-check"></i> ${escapeHtml(t('notion.clearKeyPendingButton', 'Deletion pending'))}`
      : `<i class="iconoir-trash"></i> ${escapeHtml(t('notion.clearBackendKey', 'Delete backend key'))}`;
  }
  setText('notion-clear-key-state', clearKeyRequested
    ? t('notion.clearKeyPendingState', 'The stored backend-local key will be removed on the next save.')
    : t('notion.noClearPending', 'No deletion pending.'));
}

function getCurrentStudyName() {
  return callbacks.getCurrentStudyName?.() || t('admin.unnamedStudy', 'Untitled study');
}

function setText(id, value) {
  const target = $(id);
  if (target) target.textContent = value;
}

/**
 * This study's own Notion key.
 *
 * Kept out of `saveStudyNotionSettings` on purpose: the study config travels
 * in the exported .study-runner file, and a credential must never go with it.
 * Keys go to the local secrets store through their own endpoint instead.
 */
async function saveStudyNotionKey() {
  const studyId = callbacks.getCurrentStudyName?.();
  const input = $('notion-study-api-key');
  if (!studyId || !input) return;

  const value = input.value.trim();
  if (!value) {
    callbacks.showToast?.(t('studySettings.keyEmpty', 'Enter a key first, or use Delete to remove it.'), 'error');
    return;
  }
  await writeStudyCredentials(studyId, { notion: value }, t('studySettings.keySaved', 'Key saved for this study'));
  input.value = '';
}

async function clearStudyNotionKey() {
  const studyId = callbacks.getCurrentStudyName?.();
  if (!studyId) return;
  await writeStudyCredentials(studyId, { clear_notion: true }, t('studySettings.keyCleared', "This study's key was deleted"));
  const input = $('notion-study-api-key');
  if (input) input.value = '';
}

async function writeStudyCredentials(studyId, body, successMessage) {
  try {
    const response = await postJson(`/api/admin/studies/${encodeURIComponent(studyId)}/credentials`, body);
    if (response?.ok === false) throw new Error(response.error || '');
    callbacks.showToast?.(successMessage, 'success');
    await refreshStudyKeyState(studyId);
  } catch (error) {
    callbacks.showToast?.(String(error.message || error) || t('studySettings.keyFailed', 'The key could not be saved.'), 'error');
  }
}

/** Say whether a key is in place and whose it is; never show the value. */
export async function refreshStudyKeyState(studyId = callbacks.getCurrentStudyName?.()) {
  const state = $('notion-study-key-state');
  if (!state || !studyId) return;
  try {
    const response = await getJson(`/api/admin/studies/${encodeURIComponent(studyId)}/credentials`);
    // `credentials` is keyed by kind; `scope` says where the key comes from.
    const notion = response?.credentials?.notion || {};
    if (notion.scope === 'study') {
      state.textContent = t('studySettings.keyFromStudy', 'This study has its own key.');
    } else if (notion.scope === 'env') {
      state.textContent = t('studySettings.keyFromEnv', 'An environment variable is overriding the key.');
    } else if (notion.configured) {
      state.textContent = t('studySettings.keyFromShared', 'Using the shared key from the settings hub.');
    } else {
      state.textContent = t('studySettings.keyMissing', 'No key configured yet.');
    }
  } catch {
    state.textContent = '';
  }
}
