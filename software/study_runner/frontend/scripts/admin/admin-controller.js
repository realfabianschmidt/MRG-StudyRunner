import { getJson, postJson } from '../shared/api-client.js';
import { initializeAdminDashboard } from './admin-dashboard-controller.js';
import { initializeNotionSettings } from '../settings/study/notion-settings-controller.js';
import { initializeNextcloudSettings } from '../settings/study/nextcloud-settings-controller.js';
import { initializeCertificateSettings } from '../settings/machine/certificate-settings-controller.js';
import { initializeBrandingSettings } from '../settings/machine/branding-settings-controller.js';
import { loadBranding, renderGroupLogo } from '../shared/branding.js';
import { initializeSessionsBrowser, loadCompletedSessions } from './sessions-browser.js';
import { initializeUploadMonitor } from './upload-monitor.js';
import { initializeRecoveryPanel, loadRecoveryCandidates } from './recovery-panel.js';
import { defaultStudySettings, normalizeStudySettings } from '../shared/study-settings.js';
import { transitionToView } from '../shared/view-transition.js';
import { confirmWithModal } from '../shared/modal.js';
import {
  initializeMachineSettingsPanel,
  isSettingsHubOpen,
  openSettingsHub,
  renderSettingsHubShell,
} from '../settings/machine/machine-settings-panel.js';
import { initializeStudySettingsPanel, openStudySettingsPanel } from '../settings/study/study-settings-panel.js';
import { CARDS, CARD_TYPES, defaultFor } from '../cards/index.js';
import {
  collectInfo,
  renderEditorToggles,
  renderInfoBottom,
  renderInstructionField,
  renderNoteField,
  renderPromptField,
} from '../cards/card-info.js';
import { initI18n, setLanguage, getLanguage, t } from '../shared/i18n.js';
import { createQrSvg } from '../shared/qr-code.js';
import { escapeHtml, setText } from '../shared/dom-utils.js';
import { loadPluginCatalog } from '../shared/plugin-catalog.js';

const STUDY_RUN_POLL_INTERVAL_MS = 1500;

// Load the saved or default UI language and wire the EN/DE switcher.
// A locale failure must never break the admin page, so failures are swallowed.
async function setupLanguage() {
  try {
    await initI18n();
  } catch (error) {
    console.error('[admin] Could not load translations:', error);
  }
  const switcher = document.getElementById('lang-switcher');
  if (!switcher) return;
  const markActive = () => {
    switcher.querySelectorAll('.lang-btn').forEach((button) => {
      button.classList.toggle('active', button.dataset.lang === getLanguage());
    });
  };
  switcher.querySelectorAll('.lang-btn').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await setLanguage(button.dataset.lang);
      } catch (error) {
        console.error('[admin] Could not switch language:', error);
      }
      markActive();
    });
  });
  markActive();
}

const state = {
  config: {},
  runtimeInfo: null,
  selectedIndex: null,
  loaded: false,
  draggedElement: null,
  suppressListClick: false,
  accessQrKind: null,
  updateStatus: null,
  updatePollTimer: null,
  studyRunState: null,
  tabletGate: null,
  studyRunPollTimer: null,
  settingsHubStatus: null,
  settingsHubActiveTab: 'tablet',
  pluginSettings: {},
};


const $ = (id) => document.getElementById(id);

async function init() {
  await setupLanguage();
  bindEvents();
  initializeAdminDashboard({ showToast, openSettingsHub });
  // `state` goes over by reference: the settings hub's status fetch is the same
  // one the dashboard reads, and copying it would give the two views separate
  // - and quickly diverging - pictures of the machine.
  initializeMachineSettingsPanel({
    state,
    switchView,
    showToast,
    renderStudyRunState,
    getAccessUrl,
    loadUpdateStatus,
    createDesktopShortcut,
  });
  initializeNotionSettings({
    showToast,
    switchView,
    getStudyConfig: () => state.config,
    setStudySettings: (settings) => {
      state.config.study_settings = normalizeStudySettings(settings);
      markUnsaved();
    },
    getCurrentStudyName,
    saveStudyConfig: saveConfig,
  });
  initializeNextcloudSettings({
    showToast,
    switchView,
    openStudySettingsPanel,
    getStudyConfig: () => state.config,
    setStudySettings: (settings) => {
      state.config.study_settings = normalizeStudySettings(settings);
      markUnsaved();
    },
    getCurrentStudyName,
    saveStudyConfig: saveConfig,
  });
  initializeCertificateSettings({ showToast, switchView });
  initializeBrandingSettings({
    showToast,
    confirmWithModal,
    // Re-render the hub mark straight away so the operator sees the change
    // without reopening the page.
    onBrandingChanged: (branding) => renderGroupLogo($('hub-brand-logo'), branding),
  });
  void applyHubBranding();
  initializeStudySettingsPanel({
    showToast,
    switchView,
    getStudyConfig: () => state.config,
    setStudySettings: (settings) => {
      state.config.study_settings = normalizeStudySettings(settings);
      markUnsaved();
    },
    getCurrentStudyName,
    saveStudyConfig: saveConfig,
    downloadCurrentStudy: () => void downloadStudy(getCurrentStudyName()),
  });
  initializeSessionsBrowser({ showToast, switchView });
  initializeUploadMonitor({ showToast, onLocalCompletion: () => void loadCompletedSessions() });
  initializeRecoveryPanel({ showToast, onFinalized: loadCompletedSessions });

  try {
    await loadRuntimeInfo();
    const [config] = await Promise.all([
      getJson('/api/config'),
      loadPluginCatalog(),
    ]);
    state.studyRunState = config._runtime?.study_run_state || null;
    applyLoadedConfig(config);
    await loadRecentStudies();
    await loadStudyRunState();
    await loadStudyReadiness();
    startStudyRunPolling();
    await loadCompletedSessions();
    await loadRecoveryCandidates();
    await loadUpdateStatus({ silent: true });
    state.loaded = true;
    showToast(t('toast.studyLoaded', 'Study loaded'), 'info');
  } catch (error) {
    console.error('[admin] Could not load configuration:', error);
    showToast(t('toast.loadFailed', 'Could not load the study'), 'error');
  }
}

function updateHubTitle() {
  const studyId = $('cfg-id').value.trim() || t('admin.unnamedStudy', 'Untitled study');
  const hubTitle = $('hub-active-title');
  if (hubTitle) hubTitle.textContent = studyId;
  renderStudyRunState();
}
function getCurrentStudyName() {
  return $('cfg-id').value.trim() || state.config.study_id || t('admin.unnamedStudy', 'Untitled study');
}

function applyLoadedConfig(config) {
  config.study_settings = normalizeStudySettings(config.study_settings);
  ensureBookends(config.questions);
  state.config = config;
  $('cfg-id').value = config.study_id || '';
  updateHubTitle();
  rebuildAll();
  state.loaded = true;
}

async function loadStudyRunState(options = {}) {
  try {
    const response = await getJson('/api/admin/study-run');
    state.studyRunState = response?.run_state || null;
    state.tabletGate = response?.tablet_gate || null;
    renderStudyRunState();
    return state.studyRunState;
  } catch (error) {
    if (!options.silent) {
      console.error('[admin] Could not load study run state:', error);
    }
    renderStudyRunState();
    return null;
  }
}

function startStudyRunPolling() {
  if (state.studyRunPollTimer !== null) {
    window.clearInterval(state.studyRunPollTimer);
  }
  state.studyRunPollTimer = window.setInterval(() => {
    if (!document.hidden) {
      void loadStudyRunState({ silent: true });
    }
  }, STUDY_RUN_POLL_INTERVAL_MS);
}

/**
 * Load the pre-run check for the loaded study.
 *
 * Without it a study imported from another computer runs happily and only
 * fails to upload afterwards, into the retry queue, long after the participant
 * has left. Cheap and purely config-based, so it can run after every save.
 */
async function loadStudyReadiness() {
  try {
    state.readiness = await getJson('/api/admin/study-readiness', { timeoutMs: 2000 });
  } catch (error) {
    // Never let a failed check block the operator; just show no warning.
    console.debug('[admin] Could not load study readiness:', error);
    state.readiness = null;
  }
  renderStudyRunState();
}

/**
 * The CTA chain: a marker next to the study name, and the route to the fix
 * highlighted at every step (hub -> editor -> the panel that owns the problem).
 */
function renderReadinessCta() {
  const blockers = state.readiness?.ready === false ? (state.readiness.blockers || []) : [];
  const marker = $('hub-readiness-marker');
  if (marker) {
    marker.hidden = blockers.length === 0;
    marker.title = blockers.length ? readinessSummary(blockers) : '';
  }
  // Edit is the way to every fix, so it carries the call to action on the hub.
  $('btn-hub-editor')?.classList.toggle('is-cta', blockers.length > 0);
  // Inside the editor, the study settings button is the next step.
  $('btn-study-settings')?.classList.toggle('is-cta', blockers.length > 0);
}

function readinessSummary(blockers) {
  return blockers.map((blocker) => readinessMessage(blocker)).join('\n');
}

function readinessMessage(blocker) {
  const sensorLabel = blocker.sensor ? t(`dashboard.${blocker.sensor}`, blocker.sensor) : '';
  const supportedModes = Array.isArray(blocker.supported_modes) ? blocker.supported_modes.join(', ') : '';
  const messages = {
    notion_api_key_missing: t('readiness.notionKeyMissing', 'Notion upload is on, but no API key is available for this study.'),
    notion_target_missing: t('readiness.notionTargetMissing', 'Notion upload is on, but no parent page or database is set.'),
    notion_machine_disabled: t('readiness.notionMachineDisabled', 'Notion upload is on for this study, but switched off on this computer.'),
    nextcloud_link_missing: t('readiness.nextcloudLinkMissing', 'Nextcloud upload is on, but no share link is set.'),
    sensor_machine_disabled: t('readiness.sensorMachineDisabled', '{sensor} is used by this study but switched off on this computer.').replace('{sensor}', sensorLabel),
    camera_requires_https: t('readiness.cameraRequiresHttps', 'The tablet camera needs a secure connection, which is currently off.'),
    browser_source_requires_https: t('readiness.browserSourceRequiresHttps', 'A selected browser sensor needs a secure HTTPS connection, which is currently off.'),
    plugin_mode_unsupported: t(
      'readiness.pluginModeUnsupported',
      '{sensor} mode {mode} is unavailable on {platform}. Supported: {supported}.',
    )
      .replace('{sensor}', sensorLabel || blocker.plugin || '')
      .replace('{mode}', blocker.mode || '')
      .replace('{platform}', blocker.platform || '')
      .replace('{supported}', supportedModes),
  };
  return messages[blocker.code] || blocker.code;
}

async function applyHubBranding() {
  renderGroupLogo($('hub-brand-logo'), await loadBranding());
}

function renderStudyRunState() {
  const runState = state.studyRunState || {};
  const tabletGate = state.tabletGate || {};
  const status = runState.status || 'loaded';
  const label = $('hub-active-label');
  const hint = $('hub-active-run-hint');
  const startButton = $('btn-hub-start-study');
  const startLabel = $('btn-hub-start-study-label');
  const dashboardButton = $('btn-admin-dashboard');

  if (label) {
    label.removeAttribute('data-i18n');
    label.textContent = runStatusLabel(status);
  }
  if (hint) {
    hint.removeAttribute('data-i18n');
    hint.textContent = runStatusHint(status, runState);
  }
  if (startButton) {
    const running = status === 'running';
    const gateBlocksStart = status !== 'running' && tabletGate.can_start !== true;
    // Keep Play visible so a click can explain the blocker. The backend is the
    // authoritative gate for required plugins and recording infrastructure.
    const notReady = status !== 'running' && state.readiness?.ready === false;
    startButton.disabled = running || !getCurrentStudyName() || gateBlocksStart;
    startButton.classList.toggle('is-running', running);
    startButton.classList.toggle('is-blocked', gateBlocksStart || notReady);
  }
  renderReadinessCta();
  if (startLabel) {
    startLabel.textContent = status === 'running'
      ? t('hub.runRunning', 'Running')
      : t('hub.startStudy', 'Start study');
  }
  // The dashboard is reachable at any time on purpose: sensors can be started
  // and tested from it before pressing Play, which is where setup problems are
  // actually fixed. Only the run-specific readouts idle until a study runs.
  if (dashboardButton) {
    dashboardButton.hidden = false;
  }
}

function runStatusLabel(status) {
  if (status === 'running') return t('hub.runStatus.running', 'RUNNING');
  if (status === 'completed') return t('hub.runStatus.completed', 'COMPLETED');
  if (status === 'stopped') return t('hub.runStatus.stopped', 'STOPPED');
  return t('hub.runStatus.loaded', 'LOADED');
}

function runStatusHint(status, runState) {
  const gateHint = tabletGateHint(state.tabletGate);
  if (status === 'running') {
    return gateHint || t('hub.runHint.running', 'The participant tablet can enter the study now.');
  }
  if (status === 'completed') {
    return gateHint || t('hub.runHint.completed', 'The last run was saved. Start again when the tablet should continue.');
  }
  if (status === 'stopped') {
    return gateHint || t('hub.runHint.stopped', 'The run was stopped. The tablet waits for the next start.');
  }
  if (runState?.study_id) {
    return gateHint || t('hub.runHint.loaded', 'Loaded on the tablet as a waiting room until you press Play.');
  }
  return t('hub.runHint.empty', 'Load a study, then press Play when the tablet is ready.');
}

function tabletGateHint(tabletGate) {
  const status = tabletGate?.status || '';
  if (status === 'ready') {
    return t('hub.tabletGate.ready', 'One tablet is waiting. Press Play to start it.');
  }
  if (status === 'waiting_for_tablet') {
    return t('hub.tabletGate.waiting', 'Open the participant page on one tablet before pressing Play.');
  }
  if (status === 'conflict') {
    return t('hub.tabletGate.conflict', 'More than one tablet is connected. Keep only the tablet that should run this study.');
  }
  if (status === 'assigned_missing') {
    return t('hub.tabletGate.assignedMissing', 'The assigned tablet is no longer visible. Stop or reload before starting again.');
  }
  return '';
}

/**
 * Play, from the editor.
 *
 * Starting a run from the editor is a different act than starting it from the
 * hub: the operator is mid-edit and the tablet is about to be handed over, so
 * it asks first, saves whatever is unsaved, and then leaves the editor for the
 * dashboard - which is where a running study is actually watched.
 */
async function confirmAndStartFromEditor() {
  const proceed = await confirmWithModal({
    title: getCurrentStudyName(),
    message: t('workspace.startConfirm', 'The tablet can join as soon as the study is running. Unsaved changes are saved first.'),
    confirmLabel: t('workspace.startConfirmAction', 'Start study'),
    cancelLabel: t('common.cancel', 'Cancel'),
  });
  if (!proceed) return;
  await startLoadedStudyRun({ buttonId: 'btn-workspace-start', goToDashboard: true });
}

/**
 * Start the loaded study.
 *
 * `buttonId` because two controls do this now - the hub's start button and the
 * editor's play button - and the spinner belongs on whichever one was pressed.
 * `then` decides where the operator lands afterwards.
 */
async function startLoadedStudyRun({ buttonId = 'btn-hub-start-study', goToDashboard = false } = {}) {
  if (state.readiness?.start_blocked === true) {
    const blockers = state.readiness.blockers || [];
    const message = `${t('readiness.confirmTitle', 'This study is not fully set up:')} `
      + `${readinessSummary(blockers)} `
      + t('readiness.blockedBody', 'Start is blocked until every required plugin and the recording infrastructure are ready.');
    showToast(message, 'error');
    return;
  }

  // Destination warnings are overridable because the local scientific commit
  // remains possible and publishing can be retried after the session.
  if (state.readiness?.ready === false) {
    const blockers = state.readiness.blockers || [];
    const message = `${t('readiness.confirmTitle', 'This study is not fully set up:')}\n\n`
      + `${readinessSummary(blockers)}\n\n`
      + t('readiness.confirmBody', 'Measurements are saved locally either way, but the uploads listed above will fail. Start anyway?');
    const proceed = await confirmWithModal({
      title: t('readiness.confirmTitle', 'This study is not fully set up:'),
      message,
      confirmLabel: t('readiness.confirmStart', 'Start anyway'),
      cancelLabel: t('common.cancel', 'Cancel'),
    });
    if (!proceed) return;
  }

  const button = $(buttonId);
  const previousHtml = button?.innerHTML || '';
  if (button) {
    button.disabled = true;
    button.innerHTML = `<i class="iconoir-refresh"></i><span>${escapeHtml(t('hub.startingStudy', 'Starting...'))}</span>`;
  }

  try {
    if ($('btn-save-config')?.classList.contains('btn-primary--dirty')) {
      const saved = await saveConfig({ skipToast: true });
      if (saved === false) return;
    }
    const response = await postJson('/api/admin/study-run/start', {}, { timeoutMs: 2000 });
    state.studyRunState = response?.run_state || null;
    state.tabletGate = response?.tablet_gate || state.tabletGate;
    renderStudyRunState();
    showToast(t('toast.studyStarted', 'Study started'), 'success');
    if (goToDashboard) await switchView('view-dashboard');
  } catch (error) {
    console.error('[admin] Could not start study run:', error);
    showToast(error.message || t('toast.studyStartFailed', 'Could not start the study'), 'error');
  } finally {
    if (button) {
      button.innerHTML = previousHtml;
      renderStudyRunState();
    }
  }
}

/**
 * The single funnel for every admin view change.
 *
 * Everything that opens a view goes through here - hub buttons, the settings
 * controllers, the session browser - so wrapping it in the sweep is what gives
 * every one of them the same transition. `onCovered` runs while the screen is
 * opaque, which is where a view's first data load belongs.
 *
 * `animate: false` is for programmatic switches that are not a user navigation
 * (a poll-driven correction, for example) - sweeping those would flash the
 * screen white for no reason.
 */
function switchView(viewId, { animate = true, onCovered } = {}) {
  const apply = async () => {
    document.querySelectorAll('.admin-view').forEach(el => {
      el.hidden = el.id !== viewId;
      el.classList.toggle('active', el.id === viewId);
    });
    await onCovered?.();
  };
  return animate ? transitionToView(apply) : apply();
}

function startNewStudy() {
  const studyName = t('admin.newStudyDefault', 'New study');
  state.config = {
    study_id: studyName,
    questions: [defaultFor('participant-id'), defaultFor('finish')],
    study_settings: defaultStudySettings(),
  };
  state.studyRunState = { status: 'loaded', study_id: studyName };
  state.tabletGate = null;
  $('cfg-id').value = studyName;
  updateHubTitle();
  rebuildAll();
  markUnsaved();
  showToast(t('toast.studyCreated'), 'info');

  // Open the editor immediately after creating the study.
  switchView('view-workspace');
}

function openTypePicker() {
  $('overlay-type-tag').innerHTML = `<i class="iconoir-plus"></i> ${escapeHtml(t('question.addTag', 'Add question'))}`;

  $('editor-fields').innerHTML = `
    <div class="type-picker-title">${escapeHtml(t('question.chooseType', 'Choose question type'))}</div>
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
  $('btn-hub-editor').addEventListener('click', () => switchView('view-workspace'));
  $('btn-admin-dashboard').addEventListener('click', () => switchView('view-dashboard'));
  $('btn-hub-start-study')?.addEventListener('click', () => void startLoadedStudyRun());
  $('btn-workspace-start')?.addEventListener('click', () => void confirmAndStartFromEditor());
  $('btn-hub-settings')?.addEventListener('click', () => void openSettingsHub());
  $('btn-create-shortcut')?.addEventListener('click', () => void createDesktopShortcut('btn-create-shortcut', 'shortcut-result'));
  $('btn-machine-settings-back')?.addEventListener('click', () => void switchView('view-hub'));
  $('btn-workspace-home').addEventListener('click', () => switchView('view-hub'));
  $('btn-admin-edit-view').addEventListener('click', () => switchView('view-hub'));
  $('btn-create-shortcut')?.addEventListener('click', () => createDesktopShortcut());

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

  $('btn-study-settings').addEventListener('click', () => void openStudySettingsPanel());

  $('btn-admin-qr-url')?.addEventListener('click', () => openAccessQrModal('admin'));
  $('btn-participant-qr-url')?.addEventListener('click', () => openAccessQrModal('participant'));
  $('btn-copy-admin-url')?.addEventListener('click', () => copyAccessUrl('admin'));
  $('btn-copy-participant-url')?.addEventListener('click', () => copyAccessUrl('participant'));
  $('btn-update-check')?.addEventListener('click', () => checkForPythonUpdate());
  $('btn-update-download')?.addEventListener('click', () => downloadPythonUpdate());
  $('btn-update-install')?.addEventListener('click', () => installPythonUpdate());
  $('btn-close-access-qr')?.addEventListener('click', closeAccessQrModal);
  $('access-qr-modal')?.addEventListener('click', (event) => {
    if (event.target === $('access-qr-modal')) closeAccessQrModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('access-qr-modal')?.hidden) {
      closeAccessQrModal();
    }
  });
  document.addEventListener('languagechange', () => {
    if (state.accessQrKind && !$('access-qr-modal')?.hidden) {
      updateAccessQrModalText(state.accessQrKind, getAccessUrl(state.accessQrKind));
    }
    renderStudyRunState();
    if (isSettingsHubOpen()) {
      renderSettingsHubShell();
    }
  });
}

async function loadRuntimeInfo() {
  try {
    state.runtimeInfo = await getJson('/api/runtime-info');
    renderAccessInfo();
  } catch (error) {
    console.error('[admin] Could not load runtime info:', error);
    renderAccessInfoError();
  }
}

function renderAccessInfo() {
  const info = state.runtimeInfo || {};
  const adminUrl = info.admin_url || `${window.location.origin}/admin`;
  const participantUrl = info.participant_url || window.location.origin;
  const adminTarget = $('access-admin-url');
  const participantTarget = $('access-participant-url');
  const hint = $('access-hint');

  if (adminTarget) {
    adminTarget.removeAttribute('data-i18n');
    adminTarget.textContent = adminUrl;
  }
  if (participantTarget) {
    participantTarget.removeAttribute('data-i18n');
    participantTarget.textContent = participantUrl;
  }
  if (hint) {
    hint.removeAttribute('data-i18n');
    const mode = info.app_mode ? `${t('access.mode', 'Mode')}: ${info.app_mode}. ` : '';
    const dataDir = info.data_dir
      ? `${t('access.dataFolder', 'Data folder')}: ${info.data_dir}`
      : t('access.hint', 'Use the participant link from a tablet or browser on the same private network.');
    hint.textContent = `${mode}${dataDir}`;
  }
}

function renderAccessInfoError() {
  const adminTarget = $('access-admin-url');
  const participantTarget = $('access-participant-url');
  const hint = $('access-hint');
  if (adminTarget) {
    adminTarget.removeAttribute('data-i18n');
    adminTarget.textContent = `${window.location.origin}/admin`;
  }
  if (participantTarget) {
    participantTarget.removeAttribute('data-i18n');
    participantTarget.textContent = window.location.origin;
  }
  if (hint) {
    hint.removeAttribute('data-i18n');
    hint.textContent = t('access.runtimeUnavailable', 'Runtime info is unavailable. The current browser origin is shown as fallback.');
  }
}

function openAccessQrModal(kind) {
  const url = getAccessUrl(kind);
  if (!url) {
    showToast(t('toast.noLink'), 'error');
    return;
  }

  state.accessQrKind = kind;
  updateAccessQrModalText(kind, url);
  renderAccessQrCode(url);
  $('access-qr-modal').hidden = false;
  $('btn-close-access-qr')?.focus();
}

function closeAccessQrModal() {
  $('access-qr-modal').hidden = true;
  state.accessQrKind = null;
}

function updateAccessQrModalText(kind, url) {
  const title = kind === 'admin'
    ? t('access.qrTitleAdmin', 'Admin QR code')
    : t('access.qrTitleParticipant', 'Participant QR code');
  const label = kind === 'admin'
    ? t('access.admin', 'Admin')
    : t('access.participant', 'Participant');

  $('access-qr-title').textContent = title;
  $('access-qr-label').textContent = label;
  $('access-qr-url').textContent = url || '';
  $('access-qr-url').title = url || '';
}

function renderAccessQrCode(url) {
  const target = $('access-qr-code');
  if (!target) return;
  try {
    target.innerHTML = createQrSvg(url, { size: 240, margin: 4 });
  } catch (error) {
    console.error('[admin] Could not render access QR code:', error);
    target.textContent = t('access.qrError', 'Could not render QR code.');
  }
}

function getAccessUrl(kind) {
  const target = kind === 'admin' ? $('access-admin-url') : $('access-participant-url');
  const value = target?.textContent?.trim() || '';
  if (!value || !/^https?:\/\//i.test(value)) {
    return '';
  }
  return value;
}

async function copyAccessUrl(kind) {
  const value = getAccessUrl(kind);
  if (!value) {
    showToast(t('toast.noLink'), 'error');
    return;
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      copyTextWithFallback(value);
    }
    showToast(t('toast.linkCopied'), 'success');
  } catch (error) {
    console.error('[admin] Could not copy link:', error);
    copyTextWithFallback(value);
    showToast(t('toast.linkCopied'), 'success');
  }
}

function copyTextWithFallback(value) {
  const input = document.createElement('textarea');
  input.value = value;
  input.setAttribute('readonly', '');
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  input.select();
  document.execCommand('copy');
  input.remove();
}

async function loadUpdateStatus({ silent = false } = {}) {
  try {
    const status = await getJson('/api/admin/update/status');
    state.updateStatus = status;
    renderUpdateStatus(status);
  } catch (error) {
    console.error('[admin] Could not load update status:', error);
    renderUpdateStatusError(error);
    if (!silent) {
      showToast(t('update.statusFailed', 'Update status failed'), 'error');
    }
  }
}

async function checkForPythonUpdate() {
  setUpdateBusy(true);
  try {
    const status = await postJson('/api/admin/update/check', {});
    state.updateStatus = status;
    renderUpdateStatus(status);
    const available = Boolean(status.update?.available);
    showToast(available ? t('update.availableToast', 'Update available') : t('update.currentToast', 'Study Runner is current'), available ? 'info' : 'success');
  } catch (error) {
    console.error('[admin] Update check failed:', error);
    showToast(error.message || t('update.checkFailed', 'Update check failed'), 'error');
    await loadUpdateStatus({ silent: true });
  } finally {
    setUpdateBusy(false);
  }
}

async function downloadPythonUpdate() {
  const version = state.updateStatus?.update?.version || '';
  const message = t('update.downloadConfirm', 'Download and verify update {version}?').replace('{version}', version);
  const proceed = await confirmWithModal({
    title: t('update.downloadTitle', 'Download update'),
    message,
    confirmLabel: t('update.download', 'Download'),
    cancelLabel: t('common.cancel', 'Cancel'),
  });
  if (!proceed) {
    return;
  }

  setUpdateBusy(true);
  startUpdatePolling();
  try {
    const status = await postJson('/api/admin/update/download', {});
    state.updateStatus = status;
    renderUpdateStatus(status);
    showToast(t('update.downloadedToast', 'Update downloaded and verified'), 'success');
  } catch (error) {
    console.error('[admin] Update download failed:', error);
    showToast(error.message || t('update.downloadFailed', 'Update download failed'), 'error');
    await loadUpdateStatus({ silent: true });
  } finally {
    stopUpdatePolling();
    setUpdateBusy(false);
  }
}

async function installPythonUpdate() {
  const message = t('update.restartConfirm', 'Restart Study Runner into the staged update now?');
  const proceed = await confirmWithModal({
    title: t('update.installTitle', 'Restart and install'),
    message,
    confirmLabel: t('update.install', 'Restart now'),
    cancelLabel: t('common.cancel', 'Cancel'),
  });
  if (!proceed) {
    return;
  }

  setUpdateBusy(true);
  try {
    const status = await postJson('/api/admin/update/install', {});
    state.updateStatus = status;
    renderUpdateStatus(status);
    showToast(t('update.restartingToast', 'Restarting into update ...'), 'info');
  } catch (error) {
    console.error('[admin] Update install failed:', error);
    showToast(error.message || t('update.installFailed', 'Update restart failed'), 'error');
    await loadUpdateStatus({ silent: true });
    setUpdateBusy(false);
  }
}

function startUpdatePolling() {
  stopUpdatePolling();
  state.updatePollTimer = window.setInterval(() => {
    void loadUpdateStatus({ silent: true });
  }, 900);
}

function stopUpdatePolling() {
  if (state.updatePollTimer) {
    window.clearInterval(state.updatePollTimer);
    state.updatePollTimer = null;
  }
}

function setUpdateBusy(isBusy) {
  ['btn-update-check', 'btn-update-download', 'btn-update-install'].forEach((id) => {
    const button = $(id);
    if (button) {
      button.disabled = Boolean(isBusy);
    }
  });
}

function renderUpdateStatusError(error) {
  const pill = $('update-status-pill');
  const versionLine = $('update-version-line');
  const detail = $('update-detail');
  if (pill) {
    pill.className = 'status-pill status-pill--error';
    pill.textContent = t('update.error', 'Error');
  }
  if (versionLine) {
    versionLine.textContent = t('update.unavailable', 'Unavailable');
  }
  if (detail) {
    detail.textContent = error?.message || t('update.statusFailed', 'Update status failed');
  }
  setUpdateActions({});
  setUpdateProgress(null);
}

function renderUpdateStatus(status) {
  const pill = $('update-status-pill');
  const versionLine = $('update-version-line');
  const detail = $('update-detail');
  if (!pill || !versionLine || !detail) {
    return;
  }

  const stateName = status.state || 'idle';
  const available = Boolean(status.update?.available);
  const version = status.update?.version || status.current_version || '';
  const staged = Boolean(status.staged?.version);

  let pillState = 'waiting';
  let pillText = t('update.idle', 'Idle');
  let line = t('update.versionLine', 'Installed {version}').replace('{version}', status.current_version || '-');
  let message = t('update.idleDetail', 'Check GitHub Releases for Python-only updates.');

  if (!status.configured) {
    pillState = 'disabled';
    pillText = status.source_mode ? t('update.sourceMode', 'Source mode') : t('update.disabled', 'Disabled');
    message = status.configuration_error || (
      status.source_mode
        ? t('update.sourceModeDetail', 'Source checkouts update through git pull or a fresh release ZIP.')
        : t('update.notConfiguredDetail', 'No Python updater public key is configured for this build.')
    );
  } else if (stateName === 'error' || stateName === 'install_failed') {
    pillState = 'error';
    pillText = t('update.error', 'Error');
    message = status.error || t('update.statusFailed', 'Update status failed');
  } else if (stateName === 'downloading') {
    pillState = 'starting';
    pillText = t('update.downloading', 'Downloading');
    message = formatUpdateDownload(status.download);
  } else if (stateName === 'verifying') {
    pillState = 'starting';
    pillText = t('update.verifying', 'Verifying');
    message = t('update.verifyingDetail', 'Checking hash and signature.');
  } else if (stateName === 'staged' || staged) {
    pillState = 'ready';
    pillText = t('update.ready', 'Ready');
    line = t('update.readyLine', 'Version {version} staged').replace('{version}', status.staged?.version || version);
    message = status.install_supported
      ? t('update.readyDetail', 'The update is verified and ready for restart.')
      : t('update.manualRestartDetail', 'The update is staged. Automatic restart is only available in Python packaged builds.');
  } else if (stateName === 'installing') {
    pillState = 'starting';
    pillText = t('update.restarting', 'Restarting');
    message = t('update.restartingDetail', 'Study Runner is handing off to the staged update.');
  } else if (available) {
    pillState = 'ready';
    pillText = t('update.available', 'Available');
    line = t('update.availableLine', 'Version {version} available').replace('{version}', version);
    message = t('update.availableDetail', 'Download starts only after confirmation.');
  } else if (stateName === 'current') {
    pillState = 'running';
    pillText = t('update.current', 'Current');
    message = t('update.currentDetail', 'The installed Python app version is current.');
  }

  pill.className = `status-pill status-pill--${pillState}`;
  pill.textContent = pillText;
  versionLine.textContent = line;
  detail.textContent = message;
  setUpdateActions(status);
  setUpdateProgress(status.download);
}

function setUpdateActions(status) {
  const checkButton = $('btn-update-check');
  const downloadButton = $('btn-update-download');
  const installButton = $('btn-update-install');
  const notesLink = $('update-release-notes');
  const stateName = status.state || 'idle';
  const busy = ['downloading', 'verifying', 'installing'].includes(stateName);
  const hasUpdate = Boolean(status.update?.available);
  const hasStaged = Boolean(status.staged?.version);

  if (checkButton) {
    checkButton.disabled = busy || Boolean(status.source_mode) || status.configured === false;
  }
  if (downloadButton) {
    downloadButton.hidden = !status.configured || !hasUpdate || hasStaged || busy;
    downloadButton.disabled = busy;
  }
  if (installButton) {
    installButton.hidden = !hasStaged;
    installButton.disabled = busy || !status.install_supported;
  }
  if (notesLink) {
    const notesUrl = status.update?.notes_url || '';
    notesLink.hidden = !notesUrl;
    if (notesUrl) {
      notesLink.href = notesUrl;
    }
  }
}

function setUpdateProgress(download) {
  const wrap = $('update-progress');
  const fill = $('update-progress-fill');
  if (!wrap || !fill) {
    return;
  }
  const stateName = download?.state || '';
  const total = Number(download?.total_bytes || 0);
  const done = Number(download?.bytes_downloaded || 0);
  const visible = ['downloading', 'verifying', 'staged'].includes(stateName) || (total > 0 && done > 0);
  wrap.hidden = !visible;
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0;
  fill.style.width = `${percent}%`;
}

function formatUpdateDownload(download) {
  const total = Number(download?.total_bytes || 0);
  const done = Number(download?.bytes_downloaded || 0);
  if (!total) {
    return t('update.downloadingDetailUnknown', 'Downloading update ...');
  }
  return t('update.downloadingDetail', 'Downloading {done} of {total}.')
    .replace('{done}', formatBytes(done))
    .replace('{total}', formatBytes(total));
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
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
      showToast(t('toast.bookendsLocked'), 'error');
      return;
    }
    void removeQuestion(index);
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
      <button type="button" class="admin-q-drag" data-role="drag-question" draggable="${!isFixed}" ${isFixed ? 'disabled' : ''} title="${escapeHtml(t('question.dragToReorder', 'Drag to reorder'))}" aria-label="${escapeHtml(t('question.dragToReorder', 'Drag to reorder'))}">
        <i class="iconoir-menu-scale"></i>
      </button>
      <button type="button" class="del" data-role="remove-question" data-index="${questionIndex}" title="${escapeHtml(t('question.remove', 'Remove'))}" ${isFixed ? 'disabled' : ''}>
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
      <div class="q-card-study">${cardModule.renderStudy(question, questionIndex)}${renderInfoBottom(question)}</div>
      <div class="preview-card-overlay">
        <button type="button" data-role="select-card" data-index="${questionIndex}">
          <i class="iconoir-edit-pencil"></i> ${escapeHtml(t('question.edit', 'Edit'))}
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
  // The order an author actually writes a question in: what is being asked,
  // how to answer it, the card's own settings, an optional note, then the
  // switches. Cards used to supply their own prompt field and the shared
  // block was appended after, which put "Required" above the question text.
  editorEl.innerHTML = [
    renderPromptField(question, cardModule.promptPlaceholder),
    renderInstructionField(question),
    cardModule.renderEditor(question, index),
    renderNoteField(question),
    renderEditorToggles(question, cardModule.renderEditorToggles?.(question) || ''),
  ].join('');
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

  Object.assign(updated, collectInfo($('editor-fields')));

  state.config.questions[index] = updated;

  const previewWrap = $(`pc-${index}`);
  if (previewWrap) {
    previewWrap.querySelector('.q-card-study').innerHTML = cardModule.renderStudy(updated, index) + renderInfoBottom(updated);
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

async function removeQuestion(index) {
  const message = t('question.removeConfirm', 'Remove question {number}?').replace('{number}', String(index + 1));
  const proceed = await confirmWithModal({
    title: getCardLabel(state.config.questions[index]) || String(index + 1),
    message,
    confirmLabel: t('question.remove', 'Remove'),
    cancelLabel: t('common.cancel', 'Cancel'),
  });
  if (!proceed) {
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
        label.textContent = isCode ? t('stimulus.codeLabel', 'Code') : t('stimulus.urlLabel', 'URL');
      }

      let replacement;
      if (isCode) {
        replacement = document.createElement('textarea');
        replacement.className = 'se-trigger-content se-trigger-content--code';
        replacement.rows = 6;
        replacement.placeholder = t('stimulus.codePlaceholder', 'Paste {type} code here...').replace('{type}', triggerType);
        replacement.value = savedValue;
      } else {
        replacement = document.createElement('input');
        replacement.type = 'url';
        replacement.className = 'se-trigger-content';
        replacement.placeholder = t('stimulus.urlPlaceholder', 'https://...');
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

async function saveConfig(options = {}) {
  const { successMessage = t('toast.studySaved', 'Study saved'), skipToast = false } = options;
  let questions = state.config.questions || [];
  ensureBookends(questions);

  const fullConfig = {
    study_id: $('cfg-id').value.trim(),
    questions: questions,
    study_settings: normalizeStudySettings(state.config.study_settings),
  };

  try {
    const response = await postJson('/api/config', fullConfig);
    state.config = response?.config || fullConfig;

    $('btn-save-config').classList.remove('btn-primary--dirty');
    await loadRecentStudies();
    // Settings just changed - re-check what would block a run.
    void loadStudyReadiness();
    await loadStudyRunState();
    rebuildAll();
    if (!skipToast) {
      showToast(successMessage, 'success');
    }
    return true;
  } catch (error) {
    console.error('[admin] Could not save configuration:', error);
    showToast(t('toast.saveFailed'), 'error');
    return false;
  }
}

function markUnsaved() {
  if (!state.loaded) return;
  $('btn-save-config').classList.add('btn-primary--dirty');
}

let _toastTimer = null;
function showToast(message, type = 'info') {
  const icons = { success: 'iconoir-check', error: 'iconoir-xmark-circle', info: 'iconoir-info-circle', warning: 'iconoir-warning-triangle' };
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
  input.accept = '.study-runner,.json,application/json';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const config = JSON.parse(await file.text());
      state.studyRunState = { status: 'loaded', study_id: config.study_id || '' };
      applyLoadedConfig(config);
      // Import has to persist. Loading the file into the editor and marking it
      // unsaved left the study invisible in the hub, because the operator who
      // imported from the hub never sees the editor's Save button.
      await saveConfig({
        successMessage: t('toast.importedFile', 'Imported: {name}').replace('{name}', file.name),
      });
      switchView('view-workspace');
    } catch {
      showToast(t('toast.invalidJson'), 'error');
    }
  };
  input.click();
}

async function _activateStudyFromHub(id, options = {}) {
  try {
    const response = await postJson('/api/admin/study-run/load', { id });
    applyLoadedConfig(response.config || {});
    state.studyRunState = response.run_state || { status: 'loaded', study_id: state.config.study_id || id };
    state.tabletGate = response.tablet_gate || null;
    renderStudyRunState();
    showToast(t('toast.studyLoadedWaiting', 'Study loaded - tablet is waiting'), 'success');
    if (options.openEditor) {
      switchView('view-workspace');
    }
  } catch (e) {
    showToast(t('toast.loadFailed'), 'error');
  }
}

async function downloadStudy(id) {
  try {
    const config = await getJson(`/api/admin/studies/${encodeURIComponent(id)}`);
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${id}.study-runner`;
    a.click();
    URL.revokeObjectURL(url);
  } catch(e) {
    showToast(t('toast.downloadFailed'), 'error');
  }
}

async function deleteStudy(id) {
  const message = t('hub.recent.deleteConfirm', 'Delete study "{id}" permanently?').replace('{id}', id);
  const proceed = await confirmWithModal({
    title: id,
    message,
    confirmLabel: t('hub.recent.delete', 'Delete'),
    cancelLabel: t('common.cancel', 'Cancel'),
  });
  if (!proceed) return;
  try {
    const response = await fetch(`/api/admin/studies/${encodeURIComponent(id)}`, { method: 'DELETE' });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || t('toast.deleteFailed', 'Delete failed'));
    }
    showToast(t('toast.studyDeleted'), 'success');
    await loadRecentStudies();
  } catch(e) {
    showToast(t('toast.deleteFailed'), 'error');
  }
}

async function loadRecentStudies() {
  try {
    const studies = await getJson('/api/admin/studies');
    const listEl = $('hub-recent-list');
    if (!studies || studies.length === 0) {
      listEl.innerHTML = `
        <div class="hub-recent-item empty">
          <i class="iconoir-clock"></i>
          <div>${escapeHtml(t('hub.recent.empty', 'No saved studies yet.'))}</div>
        </div>`;
      return;
    }

    listEl.innerHTML = studies.map(s => `
      <div class="hub-recent-item" data-study-id="${escapeHtml(s.id)}">
        <div class="hub-recent-item-main">
          <i class="iconoir-journal-page"></i>
          <div>
            <div class="hub-recent-item-title">${escapeHtml(s.id)}</div>
            <div class="hub-recent-item-meta">${escapeHtml(t('hub.recent.modified', 'Last edited'))}: ${new Date(s.modified * 1000).toLocaleString(getLanguage())}</div>
          </div>
        </div>
        <div class="hub-recent-actions">
          <button class="btn-icon-only" data-action="load" title="${escapeHtml(t('hub.recent.load', 'Load'))}"><i class="iconoir-import"></i></button>
          <button class="btn-icon-only" data-action="edit" title="${escapeHtml(t('hub.recent.edit', 'Edit'))}"><i class="iconoir-edit-pencil"></i></button>
          <button class="btn-icon-only" data-action="download" title="${escapeHtml(t('hub.recent.download', 'Download'))}"><i class="iconoir-download"></i></button>
          <button class="btn-icon-only is-danger" data-action="delete" title="${escapeHtml(t('hub.recent.delete', 'Delete'))}"><i class="iconoir-trash"></i></button>
        </div>
      </div>
    `).join('');

    listEl.querySelectorAll('.hub-recent-item:not(.empty)').forEach(item => {
      const id = item.dataset.studyId;
      item.querySelector('.hub-recent-item-main').addEventListener('click', () => _activateStudyFromHub(id));
      item.querySelector('[data-action="load"]').addEventListener('click', () => _activateStudyFromHub(id));
      item.querySelector('[data-action="edit"]').addEventListener('click', () => _activateStudyFromHub(id, { openEditor: true }));
      item.querySelector('[data-action="download"]').addEventListener('click', () => downloadStudy(id));
      item.querySelector('[data-action="delete"]').addEventListener('click', () => deleteStudy(id));
    });
  } catch (error) {
    console.error('[admin] Could not load recent studies:', error);
  }
}

function renderCardLabel(question) {
  const label = getCardLabel(question);
  return label ? escapeHtml(label) : `<em>${escapeHtml(t('question.noText', 'no text'))}</em>`;
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

async function createDesktopShortcut(buttonId = 'btn-create-shortcut', resultId = '') {
  const button = $(buttonId);
  const previous = button?.innerHTML || '';
  if (button) {
    button.disabled = true;
    button.innerHTML = `<i class="iconoir-refresh"></i><span>${escapeHtml(t('hub.creatingShortcut', 'Creating shortcut...'))}</span>`;
  }
  try {
    const result = await postJson('/api/admin/system/create-shortcut', {});
    const message = t('hub.shortcutCreated', 'Desktop shortcut created: {path}').replace('{path}', result.path || '');
    if (resultId) setText(resultId, message);
    showToast(message, 'success');
  } catch (error) {
    console.error('[admin] Could not create desktop shortcut:', error);
    const message = error.message || t('hub.shortcutFailed', 'Could not create desktop shortcut');
    if (resultId) setText(resultId, message);
    showToast(message, 'error');
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = previous;
    }
  }
}

void init();



