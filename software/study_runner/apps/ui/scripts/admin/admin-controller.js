import { getJson, postJson } from '../shared/api-client.js';
import { initializeAdminDashboard } from './admin-dashboard-controller.js';
import { initializeCertificateSettings } from '../settings/machine/certificate-settings-controller.js';
import { initializeBrandingSettings } from '../settings/machine/branding-settings-controller.js';
import { initializeFontSettings } from '../settings/machine/font-settings-controller.js';
import { initializeDataFolderSettings } from '../settings/machine/data-folder-settings-controller.js';
import { applyFonts, loadBranding, renderGroupLogo } from '../shared/branding.js';
import { initializeSessionsBrowser, loadCompletedSessions, openSessionDetail } from './sessions-browser.js';
import { initializeUploadMonitor } from './upload-monitor.js';
import { initializeOperatorNotices } from './operator-notices.js';
import { initializeRecoveryPanel, loadRecoveryCandidates } from './recovery-panel.js';
import { defaultStudySettings, normalizeStudySettings } from '../shared/study-settings.js';
import { transitionToView } from '../shared/view-transition.js';
import { confirmWithModal, createModal } from '../shared/modal.js';
import {
  initializeMachineSettingsPanel,
  isSettingsHubOpen,
  loadSettingsHubStatus,
  openSettingsHub,
  refreshSettingsHubIfStale,
  renderSettingsHubShell,
} from '../settings/machine/machine-settings-panel.js';
import {
  initializeStudySettingsPanel,
  openStudySettingsPanel,
  refreshStudySettingsIfStale,
  translateOpenStudySettingsPanel,
} from '../settings/study/study-settings-panel.js';
import { CARDS, CARD_TYPES, cardTypeLabel, defaultFor, loadCards, assertCardsAvailable } from '../cards/index.js';
import {
  collectInfo,
  renderEditorToggles,
  renderInstructionField,
  renderNoteField,
  renderPromptField,
} from '../cards/card-info.js';
import { initI18n, preloadLanguage, setLanguage, getLanguage, t, withLanguage } from '../shared/i18n.js';
import { createQrSvg } from '../shared/qr-code.js';
import { copyText } from '../shared/clipboard.js';
import { byId, escapeHtml, setHidden, setText } from '../shared/dom-utils.js';
import { loadPluginCatalog, pluginByKey } from '../shared/plugin-catalog.js';
import { createAdminRunControl } from './admin-run-control.js';
import { createAdminUpdateHandling } from './admin-update-handling.js';
import { createAdminStudyEditor } from './admin-study-editor.js';
import { dispatchCardHook } from '../cards/card-mount.js';

const STUDY_RUN_POLL_INTERVAL_MS = 1500;

// Load the saved or default UI language and wire the EN/DE switcher.
// A locale failure must never break the admin page, so failures are swallowed.
async function setupLanguage() {
  try {
    await initI18n();
    await Promise.allSettled(['en', 'de'].map(preloadLanguage));
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
      closeLangDropdown();
      renderStudyRunState();
    });
  });
  markActive();
}

// The language icon in the header opens a small dropdown menu. Closes on
// an outside click or a second press of the toggle.
function closeLangDropdown() {
  document.getElementById('lang-switcher')?.setAttribute('hidden', '');
  document.getElementById('btn-lang-toggle')?.setAttribute('aria-expanded', 'false');
}
function setupLangDropdown() {
  const toggle = document.getElementById('btn-lang-toggle');
  const menu = document.getElementById('lang-switcher');
  if (!toggle || !menu) return;
  toggle.addEventListener('click', (event) => {
    event.stopPropagation();
    const isHidden = menu.hasAttribute('hidden');
    if (isHidden) {
      menu.removeAttribute('hidden');
      toggle.setAttribute('aria-expanded', 'true');
    } else {
      closeLangDropdown();
    }
  });
  document.addEventListener('click', (event) => {
    if (!document.getElementById('lang-dropdown')?.contains(event.target)) closeLangDropdown();
  });
}

// Per-view header context: which crumb text shows, and which right-side
// icon (settings on the hub, back everywhere else) is visible. `backTarget`
// is where the header's home icon returns to - the editor from study
// settings, the hub from everywhere else.
const HEADER_META = {
  'view-hub': { showSettings: true },
  'view-machine-settings': { crumb: ['settingsHub.title', 'Settings'], backTarget: 'view-hub' },
  'view-study-settings': { crumb: ['studySettings.title', 'Study settings'], backTarget: 'view-workspace' },
  'view-workspace': { crumb: ['workspace.studyAdmin', 'Study Admin'], backTarget: 'view-hub' },
  'view-dashboard': { crumb: ['dashboard.title', 'Dashboard'], backTarget: 'view-hub' },
  'view-session-detail': { crumb: ['sessions.title', 'Sessions'], backTarget: 'view-hub' },
};
let headerBackTarget = 'view-hub';

function updateBreadcrumbSub() {
  const sub = document.getElementById('breadcrumb-sub');
  const crumbWrap = document.getElementById('app-header-breadcrumb');
  const activeView = document.querySelector('.admin-view.active');
  if (!activeView) { if (sub) sub.textContent = ''; return; }

  // Views with their own on-page H1 keep the header's compact title hidden
  // until that H1 has scrolled up under the bar - then it "arrives" in the
  // header, tracked and compact, in place of the page's own big heading.
  const pageHeading = activeView.querySelector('.dashboard-hero h1, #study-settings-heading');
  if (crumbWrap) {
    crumbWrap.classList.toggle('crumb-main-pending', !!pageHeading);
    if (pageHeading) {
      const scrolledPast = pageHeading.getBoundingClientRect().bottom <= 72;
      crumbWrap.classList.toggle('is-scrolled', scrolledPast);
    }
  }

  if (!sub) return;
  // Prefer the section heading currently under the header (scroll-spy) over
  // the static nav-item label, so "Settings / Tablet" becomes "Settings /
  // Browser links" once that section has scrolled under the bar.
  const titles = [...activeView.querySelectorAll('.dashboard-card-title > span')]
    .filter((el) => el.getBoundingClientRect().height > 0);
  if (titles.length) {
    const offset = 90;
    let current = titles[0];
    for (const el of titles) {
      if (el.getBoundingClientRect().top - offset <= 0) current = el;
      else break;
    }
    sub.textContent = ` / ${current.textContent}`;
    return;
  }
  const activeItem = activeView.querySelector('.settings-nav-item.active span');
  sub.textContent = activeItem ? ` / ${activeItem.textContent}` : '';
}

function syncHeaderForView(viewId) {
  const meta = HEADER_META[viewId] || {};
  const crumbMain = document.getElementById('breadcrumb-main');
  const crumbWrap = document.getElementById('app-header-breadcrumb');
  if (crumbMain) crumbMain.textContent = meta.crumb ? t(meta.crumb[0], meta.crumb[1]) : '';
  if (crumbWrap) crumbWrap.hidden = !meta.crumb;
  document.getElementById('btn-hub-settings')?.toggleAttribute('hidden', !meta.showSettings);
  document.getElementById('btn-header-home')?.toggleAttribute('hidden', !meta.backTarget);
  headerBackTarget = meta.backTarget || 'view-hub';
  updateBreadcrumbSub();
}

// Settings-shell nav items get re-rendered (innerHTML) each time a panel
// opens, so a live MutationObserver on the active class is simpler and more
// robust than hooking every render call site individually.
function initHeaderSync() {
  syncHeaderForView('view-hub');
  const observer = new MutationObserver(updateBreadcrumbSub);
  observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'] });
  document.querySelectorAll('.admin-main').forEach((main) => {
    main.addEventListener('scroll', updateBreadcrumbSub, { passive: true });
  });
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
  // An empty `pluginSettings` means "not fetched yet" until this turns true,
  // which is what lets the settings shell show a placeholder instead of
  // silently omitting a plugin's whole settings block.
  pluginSettingsLoaded: false,
  settingsHubError: false,
  readiness: null,
};


const $ = (id) => document.getElementById(id);

async function init() {
  await setupLanguage();
  setupLangDropdown();
  initHeaderSync();
  bindEvents();
  initializeAdminDashboard({
    showToast,
    openSettingsHub,
    startStudy: (...args) => startLoadedStudyRun(...args),
  });
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
  initializeCertificateSettings({ showToast, switchView });
  initializeBrandingSettings({
    showToast,
    confirmWithModal,
    // Re-render the hub mark straight away so the operator sees the change
    // without reopening the page.
    onBrandingChanged: (branding) => renderGroupLogo($('hub-brand-logo'), branding),
  });
  initializeFontSettings({ showToast });
  initializeDataFolderSettings({ showToast, confirmWithModal, waitForRestartAndReload });
  void applyHubBranding();
  initializeStudySettingsPanel({
    showToast,
    switchView,
    getStudyConfig: () => state.config,
    setStudySettings: (settings) => {
      state.config.study_settings = normalizeStudySettings(settings);
      rebuildAll();
      markUnsaved();
    },
    getCurrentStudyName,
    saveStudyConfig: saveConfig,
    downloadCurrentStudy: () => void downloadStudy(getCurrentStudyName()),
  });
  initializeSessionsBrowser({ showToast, switchView });
  initializeUploadMonitor({
    showToast,
    onLocalCompletion: () => void loadCompletedSessions(),
    onOpenSession: (session) => openSessionDetail(
      session.study_id,
      session.participant_id,
      session.session_id,
      session.session_folder,
    ),
  });
  initializeOperatorNotices({ showToast });
  initializeRecoveryPanel({ showToast, onFinalized: loadCompletedSessions });

  try {
    await loadRuntimeInfo();
    const [config] = await Promise.all([
      getJson('/api/config'),
      loadPluginCatalog(),
    ]);
    // A settings shell opened during start-up was built from a catalog that
    // had not answered yet, and nothing re-rendered when it did.
    refreshSettingsHubIfStale();
    refreshStudySettingsIfStale();
    await loadCards({ requiredTypes: [...(config.questions || []).map(q => q.type), 'participant-id', 'finish'] });
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
    // Warm the settings hub while nobody is looking at it: its three fetches
    // are what the operator otherwise waits for on the first open, on a
    // server that is still starting its plugin workers.
    void loadSettingsHubStatus();
    showToast(t('toast.studyLoaded', 'Study loaded'), 'info');
  } catch (error) {
    console.error('[admin] Could not load configuration:', error);
    showToast(`${t('toast.loadFailed', 'Could not load the study')}: ${error.message}`, 'error');
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
  assertCardsAvailable([...(config.questions || []).map(q => q.type), 'participant-id', 'finish']);
  config.study_settings = normalizeStudySettings(config.study_settings);
  ensureBookends(config.questions);
  state.config = config;
  $('cfg-id').value = config.study_id || '';
  updateHubTitle();
  rebuildAll();
  state.loaded = true;
}

const {
  applyHubBranding,
  confirmAndStartFromEditor,
  currentReadinessBlockers,
  loadStudyReadiness,
  loadStudyRunState,
  openAbortStudyModal,
  openReadinessSettings,
  renderStudyRunState,
  startLoadedStudyRun,
  startStudyRunPolling,
} = createAdminRunControl({
  state,
  getJson,
  postJson,
  $,
  t,
  setText,
  setHidden,
  escapeHtml,
  pluginByKey,
  openStudySettingsPanel,
  byId,
  createModal,
  applyFonts,
  renderGroupLogo,
  loadBranding,
  getCurrentStudyName: (...args) => getCurrentStudyName(...args),
  confirmWithModal,
  saveConfig: (...args) => saveConfig(...args),
  showToast: (...args) => showToast(...args),
  switchView: (...args) => switchView(...args),
  constants: { studyRunPollIntervalMs: STUDY_RUN_POLL_INTERVAL_MS },
});

function switchView(viewId, { animate = true, onCovered } = {}) {
  const apply = async () => {
    document.querySelectorAll('.admin-view').forEach(el => {
      el.hidden = el.id !== viewId;
      el.classList.toggle('active', el.id === viewId);
    });
    syncHeaderForView(viewId);
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
          <i class="iconoir-${escapeHtml(meta.icon)}"></i>${escapeHtml(cardTypeLabel(type, meta.label))}<small>${escapeHtml(type)}</small>
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
  $('btn-hub-abort-study')?.addEventListener('click', () => void openAbortStudyModal());
  $('btn-readiness-settings')?.addEventListener('click', () => void openReadinessSettings());
  $('btn-workspace-start')?.addEventListener('click', () => void confirmAndStartFromEditor());
  $('btn-hub-settings')?.addEventListener('click', () => void openSettingsHub());
  $('btn-header-home')?.addEventListener('click', () => switchView(headerBackTarget));
  $('btn-create-shortcut')?.addEventListener('click', () => void createDesktopShortcut('btn-create-shortcut', 'shortcut-result'));

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

  // The preview column is live: the card being edited reacts like on the
  // tablet (its hooks, via cards/card-mount.js); a click on any other card
  // selects that card for editing.
  const preview = $('study-preview');
  preview.addEventListener('click', (event) => {
    const button = event.target.closest('[data-role="select-card"]');
    const wrap = event.target.closest('.preview-card-wrap');
    if (button) {
      selectQuestion(Number(button.dataset.index));
    } else if (wrap?.classList.contains('selected')) {
      withLanguage(state.config.study_settings?.participant_language || 'en', () => dispatchCardHook('onClick', event));
    } else if (wrap) {
      selectQuestion(Number(wrap.id.replace('pc-', '')));
    }
  });
  preview.addEventListener('input', (event) => {
    if (event.target.closest('.preview-card-wrap.selected')) dispatchCardHook('onInput', event);
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
    translateOpenStudySettingsPanel();
    if ($('view-workspace')?.classList.contains('active')) {
      if ($('editor-fields')?.querySelector('.type-grid')) {
        openTypePicker();
      } else if ($('admin-sidebar')?.classList.contains('has-overlay') && state.selectedIndex !== null) {
        liveUpdate(state.selectedIndex);
        rebuildAll();
        selectQuestion(state.selectedIndex);
      } else {
        rebuildAll();
      }
    }
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

  await copyText(value);
  showToast(t('toast.linkCopied'), 'success');
}

const {
  checkForPythonUpdate,
  downloadPythonUpdate,
  installPythonUpdate,
  loadUpdateStatus,
  runSourceUpdate,
  waitForRestartAndReload,
} = createAdminUpdateHandling({
  state,
  getJson,
  postJson,
  showToast: (...args) => showToast(...args),
  t,
  confirmWithModal,
  $,
});
const {
  addQuestion,
  closeOverlay,
  downloadStudy,
  ensureBookends,
  getCardLabel,
  getMeta,
  handleListClick,
  handleListDragEnd,
  handleListDragOver,
  handleListDragStart,
  handleListDrop,
  handleTriggerTypePill,
  liveUpdate,
  loadFromFile,
  loadRecentStudies,
  markUnsaved,
  rebuildAll,
  removeQuestion,
  renderCardLabel,
  saveConfig,
  selectQuestion,
  showToast,
} = createAdminStudyEditor({
  state,
  $,
  CARDS,
  CARD_TYPES,
  defaultFor,
  renderPromptField,
  renderInstructionField,
  renderNoteField,
  renderEditorToggles,
  collectInfo,
  escapeHtml,
  t,
  confirmWithModal,
  normalizeStudySettings,
  postJson,
  getJson,
  loadStudyReadiness: (...args) => loadStudyReadiness(...args),
  loadStudyRunState: (...args) => loadStudyRunState(...args),
  applyLoadedConfig: (...args) => applyLoadedConfig(...args),
  renderStudyRunState: (...args) => renderStudyRunState(...args),
  switchView: (...args) => switchView(...args),
  getLanguage,
});
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
