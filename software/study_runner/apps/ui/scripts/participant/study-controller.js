import { getJson, postJson as postJsonToServer } from '../shared/api-client.js';
import { CARDS, isAnswerless, loadCards } from '../cards/index.js';
import { resetAllCardState } from '../cards/session-state.js';
import { dispatchCardHook as dispatchSharedCardHook, mountCard } from '../cards/card-mount.js';
import { escapeHtml } from '../shared/dom-utils.js';
import { renderMediaLayout } from '../shared/rich-text.js';
import { getStudyClientId, startStudyClientHeartbeat } from './study-client-heartbeat.js';
import { setLanguage, t } from '../shared/i18n.js';
import { startDeadlineTimer, remainingWholeSeconds } from '../shared/deadline-timer.js';
import {
  createEventId,
  flushReliableStudyEvents,
  sendReliableStudyEvent as sendReliableStudyEventToServer,
} from '../shared/reliable-event-queue.js';
import {
  getPluginCatalog,
  getPluginUiExtension,
  loadPluginCatalog,
  loadPluginUiExtensions,
  pluginsWithCapability,
} from '../shared/plugin-catalog.js';
import { createParticipantPluginExtensionManager } from '../shared/participant-plugin-extensions.js';
import { startAmbientBubbles, stopAmbientBubbles } from '../shared/ambient-bubbles.js';
import { applyFonts, loadBranding, renderFunderLogos, renderGroupLogo } from '../shared/branding.js';
import { createModal } from '../shared/modal.js';
import { createParticipantSessionRecovery } from './participant-session-recovery.js';
import { createParticipantResultSubmission } from './participant-result-submission.js';
import { createParticipantStimulusExecution } from './participant-stimulus-execution.js';

// Preview renders real cards but suppresses participant claims and writes.
const IS_PREVIEW = new URLSearchParams(window.location.search).get('preview') === '1';

const PREVIEW_ALLOWED_POSTS = new Set(['/api/sync-clock']);

function postJson(url, body, options) {
  if (IS_PREVIEW && !PREVIEW_ALLOWED_POSTS.has(url)) {
    console.debug('[preview] suppressed POST', url);
    return Promise.resolve({});
  }
  return postJsonToServer(url, body, options);
}

function sendReliableStudyEvent(endpoint, payload, options) {
  if (IS_PREVIEW) {
    console.debug('[preview] suppressed event', endpoint);
    return Promise.resolve({});
  }
  return sendReliableStudyEventToServer(endpoint, payload, options);
}

function sendStudyBeacon(url, blob) {
  if (IS_PREVIEW) return false;
  return navigator.sendBeacon(url, blob);
}

const state = {
  coverVisibleRunId: '',
  coverDismissedRunId: '',
  config: {},
  sensorRuntime: {},
  startTime: null,
  sessionId: '',
  participantIdOverride: '',
  participantMetadataOverride: {},
  currentIndex: 0,
  activeStimulus: null,
  clockOffsetMs: null,  // estimated server epoch ms minus tablet performance.now()
  clockRttMs: null,
  touchedFields: {},
  questionMetrics: {},
  sensorSessionStarted: false,
  runtimePollTimer: null,
  navigationBusy: false,
  submitInFlight: false,
  studyRunState: null,
  waitingForAdminStart: false,
  questionsBuilt: false,
  activationInProgress: false,
  completedLocally: false,
  completedRunId: '',
  pendingSubmission: null,
  freshPageRequested: false,
};

const STUDY_SESSION_STATE_KEY = 'study-runner-active-session';
const PENDING_SUBMISSION_STATE_KEY = 'study-runner-pending-submission';
const RUNTIME_POLL_INTERVAL_MS = 1500;
const CLOCK_SYNC_TIMEOUT_MS = 1000;
const RUNTIME_POLL_TIMEOUT_MS = 1000;
const MARKER_TIMEOUT_MS = 1200;
const TRIAL_START_TIMEOUT_MS = 3000;
const TRIAL_PREPARE_TIMEOUT_MS = 3000;
const TRIAL_STOP_TIMEOUT_MS = 1500;
const STUDY_SESSION_STOP_TIMEOUT_MS = 1500;

const participantExtensions = createParticipantPluginExtensionManager({
  getPlugins: () => getPluginCatalog().plugins,
  isEnabled: (plugin) => isParticipantPluginEnabled(plugin),
  loadExtensions: () => loadPluginUiExtensions('participant'),
  getExtensionModule: (plugin) => getPluginUiExtension(plugin, 'participant'),
  createContext: (plugin) => createParticipantExtensionContext(plugin),
  onWarning: ({ pluginKey, hook, message }) => {
    console.warn(`[study:${pluginKey}] Optional participant extension hook ${hook} failed:`, message);
  },
});

function getElement(id) {
  return document.getElementById(id);
}

async function init() {
  bindEvents();
  initFullscreenUi();
  // The heartbeat is what claims the single tablet slot, so a preview must not
  // send one - otherwise looking at your own study blocks starting it.
  if (!IS_PREVIEW) {
    startStudyClientHeartbeat(getStudyClientHeartbeatPayload, { onHeartbeat: handleHeartbeatResponse });
  }
  bindPageLifecycleEvents();
  // Estimate clock offset in the background; the waiting room must not skip it.
  void syncClock();

  try {
    if (!IS_PREVIEW) startRuntimePolling();
    await loadStudyConfig();
    if (isWaitingForAdminStart()) {
      showWaitingForAdminStart();
      return;
    }
    if (IS_PREVIEW) {
      showPreviewBanner();
      // Preview shows the real title slide before the cards, so the operator
      // can check what the participant meets first. There is no admin to press
      // Start here, so it advances itself.
      await showPreviewWaitingSlide();
    }
    await activateStudyUiAfterAdminStart();
  } catch (error) {
    document.body.classList.remove('i18n-loading');
    console.error('[study] Could not load configuration:', error);
    showStudyNotice(`${t('study.loadFailed', 'The study could not be loaded. Please tell the study supervisor.')} ${error.message}`);
  }
}

async function loadStudyConfig() {
  const [config] = await Promise.all([
    getJson(`/api/config?client_id=${encodeURIComponent(getStudyClientId())}`),
    loadPluginCatalog(),
  ]);
  try {
    await setLanguage(config.study_settings?.participant_language || 'en', { persist: false });
  } catch (error) {
    console.error('[study] Could not load translations:', error);
  }
  document.body.classList.remove('i18n-loading');
  await loadCards({ types: [...new Set((config.questions || []).map(q => q.type))] });
  state.config = config;
  document.body.classList.toggle('study-card-frame--off', config.study_settings?.card_frame_enabled === false);
  state.studyRunState = state.config._runtime?.study_run_state || null;
  state.sensorRuntime = state.config._runtime?.sensor_runtime || {};
  // Participant extensions are optional. Their asset loading/initialization may
  // never delay the generic participant UI or its monotonic timers.
  void queueParticipantExtensionSync('config_loaded');
  void applyBranding();
}

/** In preview there is no run to wait for, so the gate is simply open. */
function isWaitingForAdminStart() {
  return !IS_PREVIEW && !isStudyRunRunning();
}

/** How long preview holds on the title slide before moving to the cards. */
const PREVIEW_WAITING_SLIDE_MS = 5000;

/**
 * Show the real waiting slide, then continue.
 *
 * Deliberately the same `showWaitingForAdminStart` a participant gets, so the
 * preview cannot drift away from what is actually shown on the tablet - it is
 * a mirror, not a second implementation of the same screen.
 */
async function showPreviewWaitingSlide() {
  showWaitingForAdminStart();
  await new Promise((resolve) => setTimeout(resolve, PREVIEW_WAITING_SLIDE_MS));
  // Leaving the flag set would make the first card think it is still gated.
  state.waitingForAdminStart = false;
}

function isStudyRunRunning(runState = state.studyRunState) {
  return runState?.status === 'running';
}

/** A permanent marker that nothing here is being recorded. */
function showPreviewBanner() {
  if (document.getElementById('study-preview-banner')) return;
  const banner = document.createElement('div');
  banner.id = 'study-preview-banner';
  banner.className = 'study-preview-banner';
  banner.textContent = t('study.previewBanner', 'Preview - nothing is recorded and no session is started.');
  document.body.appendChild(banner);
}

function showWaitingForAdminStart(options = {}) {
  state.coverVisibleRunId = '';
  state.completedLocally = false;
  state.waitingForAdminStart = true;
  state.questionsBuilt = false;
  participantExtensions.stopPrestudyMonitors({ reason: 'waiting_for_admin_start' });
  const title = getElement('study-waiting-title');
  const body = getElement('study-waiting-body');
  if (title) {
    title.textContent = options.title
      || state.config?.study_id
      || t('study.waiting.title', 'Study will start soon');
  }
  if (body) {
    body.textContent = options.body || t('study.waiting.body', 'Please keep this page open.');
  }
  showScreen('waiting');
  updateProgressBar(0, 0);
}

async function activateStudyUiAfterAdminStart() {
  if (state.activationInProgress || state.questionsBuilt) {
    return;
  }
  // The cover page stays up until "Start" is pressed; polling must not rebuild it.
  if (state.coverVisibleRunId && state.coverVisibleRunId === currentRunKey()) {
    return;
  }
  state.activationInProgress = true;
  try {
    await loadStudyConfig();
    if (isWaitingForAdminStart()) {
      showWaitingForAdminStart();
      return;
    }
    state.waitingForAdminStart = false;
    if (!hasParticipantIdStartCard()) {
      renderParticipantIdRequiredBlock();
      state.questionsBuilt = true;
      showScreen('questions');
      return;
    }
    const cover = coverPageSettings();
    if (cover && state.coverDismissedRunId !== currentRunKey() && !matchingSessionSnapshot()) {
      showCoverPage(cover);
      return;
    }
    buildQuestions({ markInitialShown: false, startFirstStimulus: false });
    state.questionsBuilt = true;
    state.waitingForAdminStart = false;
    showScreen('questions');
    const recoveryVisible = renderRecoveryBlockIfNeeded();
    if (!recoveryVisible) {
      startParticipantExtensionMonitors('prestudy_ready');
    }
    if (shouldStartStudyImmediately()) {
      void startTrial({ rebuild: false });
    }
    void requestStudyFullscreen();
  } finally {
    state.activationInProgress = false;
  }
}

// Clears everything this page holds about the current participant: every
// card's session state and the controller's own copy of the participant ID.
function resetParticipantSessionState() {
  resetAllCardState();
  state.participantIdOverride = '';
  state.participantMetadataOverride = {};
  state.pendingSubmission = null;
}

function pageHoldsSession() {
  return state.questionsBuilt || state.completedLocally || Boolean(state.startTime);
}

// The guarantee that no participant ever sees or submits data from the one
// before: when a session is over, the page is loaded again from scratch, which
// drops all JavaScript state (cards, overlays, timers, listeners) whether or
// not a card follows the session-state rule. Only the snapshot of the ended
// session is removed; a not-yet-confirmed submission stays for its own retry.
function startFreshParticipantPage(reason) {
  if (IS_PREVIEW || state.freshPageRequested || state.submitInFlight) {
    return false;
  }
  state.freshPageRequested = true;
  clearSessionSnapshot();
  state.startTime = null;
  state.sessionId = '';
  state.sensorSessionStarted = false;
  resetParticipantSessionState();
  console.info(`[study] Starting a fresh participant page (${reason}).`);
  window.location.replace(window.location.href);
  return true;
}

function handleStudyRunState(runState) {
  if (!runState || typeof runState !== 'object') {
    return;
  }
  if (state.freshPageRequested) {
    return;
  }
  const previousRunId = state.studyRunState?.run_id || '';
  const nextRunId = runState.run_id || '';
  const runChanged = Boolean(previousRunId && nextRunId && previousRunId !== nextRunId);
  const sessionOver = runChanged
    || (state.completedLocally && runState.status === 'loaded')
    || runState.status === 'aborted';
  if (sessionOver && pageHoldsSession() && startFreshParticipantPage(runChanged ? 'new_run' : runState.status)) {
    return;
  }
  state.studyRunState = runState;
  if (runState.conflict === true || runState.status === 'blocked') {
    showWaitingForAdminStart({
      title: t('study.tabletConflict.title', 'This tablet is not assigned'),
      body: runState.message || t('study.tabletConflict.body', 'Another tablet is already assigned to this study run. Please tell the study supervisor.'),
    });
    return;
  }
  if (runState.status === 'aborted') {
    const reason = runState.aborted_reason || '';
    showWaitingForAdminStart({
      title: t('study.aborted.title', 'This session was ended by the study supervisor'),
      body: reason
        ? t('study.aborted.body', 'Reason: {reason}').replace('{reason}', reason)
        : t('study.aborted.bodyNoReason', 'Please wait for the next study to begin.'),
    });
    return;
  }
  if (state.completedLocally) {
    return;
  }
  if (isStudyRunRunning(runState)) {
    if (state.waitingForAdminStart || !state.questionsBuilt) {
      void activateStudyUiAfterAdminStart();
    }
    return;
  }

  const doneVisible = getElement('screen-done')?.classList.contains('active');
  if (!state.startTime && state.questionsBuilt && !doneVisible) {
    showWaitingForAdminStart();
  }
}

/**
 * Estimate server epoch ms from tablet performance.now().
 * Runs 3 ping-pong rounds and uses the median offset.
 * Algorithm: server_minus_perf = ((srv_recv - cli_send) + (srv_send - cli_recv)) / 2
 */
const {
  bindPageLifecycleEvents,
  clearSessionSnapshot,
  closeVisibilityInterruption,
  coverPageSettings,
  currentRunKey,
  dismissCoverPage,
  estimateServerEpochMs,
  getClientClockOffsetMs,
  getSessionPayload,
  handleHeartbeatResponse,
  matchingSessionSnapshot,
  renderRecoveryBlockIfNeeded,
  reportNoticeToAdmin,
  saveSessionSnapshot,
  sendPartialResults,
  showCoverPage,
  showStudyNotice,
  startRuntimePolling,
  syncClock,
} = createParticipantSessionRecovery({
  state,
  getElement,
  getJson,
  postJson,
  sendStudyBeacon,
  flushReliableStudyEvents,
  getStudyClientId,
  resolveParticipantId: (...args) => resolveParticipantId(...args),
  collectParticipantMetadata: (...args) => collectParticipantMetadata(...args),
  collectAnswers: (...args) => collectAnswers(...args),
  collectAnswerEvents: (...args) => collectAnswerEvents(...args),
  collectCardEvents: (...args) => collectCardEvents(...args),
  updateSensorRuntime: (...args) => updateSensorRuntime(...args),
  handleStudyRunState: (...args) => handleStudyRunState(...args),
  queueParticipantExtensionSync: (...args) => queueParticipantExtensionSync(...args),
  isStudyRunRunning: (...args) => isStudyRunRunning(...args),
  activateStudyUiAfterAdminStart: (...args) => activateStudyUiAfterAdminStart(...args),
  showScreen: (...args) => showScreen(...args),
  updateProgressBar: (...args) => updateProgressBar(...args),
  buildQuestions: (...args) => buildQuestions(...args),
  markQuestionShown: (...args) => markQuestionShown(...args),
  updateNavigation: (...args) => updateNavigation(...args),
  playCardEntrance: (...args) => playCardEntrance(...args),
  prepareStimulusCard: (...args) => prepareStimulusCard(...args),
  startParticipantExtensionMonitors: (...args) => startParticipantExtensionMonitors(...args),
  renderMediaLayout,
  escapeHtml,
  t,
  constants: {
    sessionStateKey: STUDY_SESSION_STATE_KEY,
    runtimePollIntervalMs: RUNTIME_POLL_INTERVAL_MS,
    clockSyncTimeoutMs: CLOCK_SYNC_TIMEOUT_MS,
    runtimePollTimeoutMs: RUNTIME_POLL_TIMEOUT_MS,
  },
});
function bindEvents() {
  getElement('btn-prev').addEventListener('click', () => void goTo(state.currentIndex - 1));
  getElement('btn-next').addEventListener('click', () => void handleNext());
  getElement('btn-cover-start')?.addEventListener('click', dismissCoverPage);
  getElement('btn-study-fullscreen')?.addEventListener('click', () => void toggleStudyFullscreen());

  const questionContainer = getElement('q-container');
  questionContainer.addEventListener('input', handleQuestionInput);
  questionContainer.addEventListener('click', (event) => dispatchCardHook('onClick', event));
  questionContainer.addEventListener('change', handleQuestionChange);
  questionContainer.addEventListener('card:changed', handleQuestionChange);
  questionContainer.addEventListener('participantid:changed', handleQuestionChange);
}

/**
 * The participant view still enters fullscreen on its own; only the visible
 * toggle is switched off for now. Set this back to true to show the button
 * again - markup, styles, locale keys and handlers are all still in place.
 */
const SHOW_FULLSCREEN_BUTTON = false;

function initFullscreenUi() {
  const fullscreenUi = getElement('study-fullscreen-ui');
  if (!fullscreenUi || !isFullscreenSupported()) {
    return;
  }

  fullscreenUi.hidden = !SHOW_FULLSCREEN_BUTTON;
  updateFullscreenUi();

  document.addEventListener('fullscreenchange', updateFullscreenUi);
  document.addEventListener('webkitfullscreenchange', updateFullscreenUi);

  const tryEnterOnce = () => {
    document.removeEventListener('pointerdown', tryEnterOnce);
    void requestStudyFullscreen();
  };
  document.addEventListener('pointerdown', tryEnterOnce, { once: true });
}

function isFullscreenSupported() {
  const root = document.documentElement;
  return Boolean(
    root.requestFullscreen
    || root.webkitRequestFullscreen
    || document.exitFullscreen
    || document.webkitExitFullscreen
  );
}

function isStudyFullscreenActive() {
  return Boolean(document.fullscreenElement || document.webkitFullscreenElement);
}

function updateFullscreenUi() {
  const fullscreenUi = getElement('study-fullscreen-ui');
  const fullscreenButton = getElement('btn-study-fullscreen');
  const icon = fullscreenButton?.querySelector('i');
  const isActive = isStudyFullscreenActive();

  if (!fullscreenUi || !fullscreenButton || !icon) {
    return;
  }

  fullscreenUi.hidden = !SHOW_FULLSCREEN_BUTTON;
  fullscreenUi.classList.toggle('study-fullscreen-ui--active', isActive);
  fullscreenButton.setAttribute('aria-label', isActive ? t('study.exitFullscreenAria', 'Exit fullscreen') : t('study.fullscreenAria', 'Enter fullscreen'));
  fullscreenButton.title = isActive ? t('study.exitFullscreenTitle', 'Exit fullscreen') : t('study.fullscreenTitle', 'Fullscreen');
  icon.className = isActive ? 'iconoir-xmark' : 'iconoir-expand';
}

async function requestStudyFullscreen() {
  if (!isFullscreenSupported() || isStudyFullscreenActive()) {
    updateFullscreenUi();
    return;
  }

  const root = document.documentElement;
  try {
    if (root.requestFullscreen) {
      await root.requestFullscreen({ navigationUI: 'hide' });
    } else if (root.webkitRequestFullscreen) {
      root.webkitRequestFullscreen();
    }
  } catch {
    // Browser blocked programmatic fullscreen without a direct gesture.
  } finally {
    updateFullscreenUi();
  }
}

async function exitStudyFullscreen() {
  try {
    if (document.exitFullscreen) {
      await document.exitFullscreen();
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen();
    }
  } catch {
    // Ignore exit errors and keep the fallback button visible.
  } finally {
    updateFullscreenUi();
  }
}

async function toggleStudyFullscreen() {
  if (isStudyFullscreenActive()) {
    await exitStudyFullscreen();
    return;
  }
  await requestStudyFullscreen();
}

// Package 5g.B2: every card module's optional onInput/onClick hook, called
// generically instead of importing specific hooks by name (the actual leak
// that made adding a new interactive card type mean editing this file's
// import list). Every distinct module is called unconditionally, matching
// what the named imports did before - each hook already self-filters via
// its own CSS selector, so calling one that does not apply is a cheap no-op.
// participant-id is excluded: it is handled by its own explicit call below,
// which guards against re-entering on the very "participantid:changed"
// event its hash computation dispatches.
function dispatchCardHook(hookName, event) {
  // The participant-ID card's input is routed below with its own guard.
  dispatchSharedCardHook(hookName, event, { skip: [CARDS['participant-id']].filter(Boolean) });
}

function handleQuestionInput(event) {
  const target = event.target;
  const questionIndex = getQuestionIndexFromElement(target);
  if (questionIndex !== null && target?.matches('.js-slider-input')) {
    markQuestionField(questionIndex, target.id || target.name || 'slider');
  }
  if (questionIndex !== null && target?.matches('textarea.fi-textarea')) {
    markQuestionField(questionIndex, target.id || 'text');
  }

  dispatchCardHook('onInput', event);

  if (event.type !== 'participantid:changed') {
    CARDS['participant-id']?.onInput(event);
  }
  updateNavigation();
  saveSessionSnapshot();
}

function handleQuestionChange(event) {
  const questionIndex = getQuestionIndexFromElement(event.target);
  if (questionIndex !== null && event.target?.matches('input[type="radio"], input[type="checkbox"]')) {
    markQuestionField(questionIndex, event.target.id || event.target.name || 'selection');
  }
  // Cards report non-input changes (tapped words, dragged items) through
  // one generic event; see notifyCardChanged() in cards/card-info.js.
  if (questionIndex !== null && event.type === 'card:changed') {
    markQuestionField(questionIndex, event.detail?.field || 'selection');
  }
  
  if (event.type !== 'participantid:changed') {
    CARDS['participant-id']?.onInput(event);
  }
  updateNavigation();
  saveSessionSnapshot();
}

function resolveParticipantId() {
  // Once the session started, its data folder is bound to this ID. A later
  // edit on the ID card must not move the results to another participant.
  if (state.sensorSessionStarted && state.participantIdOverride) {
    return state.participantIdOverride;
  }
  const questions = state.config.questions || [];
  const pidIdx = questions.findIndex(q => q.type === 'participant-id');
  if (pidIdx >= 0) {
    return CARDS['participant-id'].collectAnswer() || state.participantIdOverride || '';
  }
  return state.participantIdOverride || 'unknown';
}

function collectParticipantMetadata() {
  const questions = state.config.questions || [];
  const pidIdx = questions.findIndex(q => q.type === 'participant-id');
  if (pidIdx >= 0) {
    const metadata = CARDS['participant-id'].collectMetadata?.() || {};
    return Object.keys(metadata).length ? metadata : state.participantMetadataOverride || {};
  }
  return state.participantMetadataOverride || {};
}

function buildEventPayload(questionIndex, question, phase, clientTriggerMs = performance.now()) {
  return {
    study_id: state.config.study_id || '',
    session_id: state.sessionId || '',
    client_id: getStudyClientId(),
    participant_id: resolveParticipantId(),
    question_index: Number.isInteger(questionIndex) ? questionIndex : null,
    question_type: question?.type || '',
    phase,
    client_trigger_ms: clientTriggerMs,
    client_trigger_epoch_ms: estimateServerEpochMs(clientTriggerMs),
    clock_offset_ms: getClientClockOffsetMs(),
    plugin_actions: getActivePluginActions(question),
  };
}

function loadPendingSubmission(sessionId) {
  if (state.pendingSubmission?.session_id === sessionId) {
    return state.pendingSubmission;
  }
  try {
    const raw = window.sessionStorage.getItem(PENDING_SUBMISSION_STATE_KEY);
    const payload = raw ? JSON.parse(raw) : null;
    if (payload?.session_id === sessionId) {
      state.pendingSubmission = payload;
      return payload;
    }
  } catch {
    // A fresh immutable payload will be prepared below.
  }
  return null;
}

function persistPendingSubmission(payload) {
  state.pendingSubmission = payload;
  try {
    window.sessionStorage.setItem(PENDING_SUBMISSION_STATE_KEY, JSON.stringify(payload));
  } catch {
    // The in-memory copy still makes retries in this page idempotent.
  }
}

function clearPendingSubmission() {
  state.pendingSubmission = null;
  try {
    window.sessionStorage.removeItem(PENDING_SUBMISSION_STATE_KEY);
  } catch {
    // Ignore storage failures after the server has acknowledged the commit.
  }
}

async function sendMarker(markerEvent, questionIndex, question, phase = markerEvent, options = {}) {
  const eventId = options.eventId || createEventId(`marker-${markerEvent}`);
  try {
    const payload = buildEventPayload(questionIndex, question, phase);
    await sendReliableStudyEvent('/api/marker', {
      ...payload,
      event_id: eventId,
      marker_event: markerEvent,
    }, { timeoutMs: MARKER_TIMEOUT_MS });
  } catch (error) {
    console.error('[study] Could not send /api/marker:', error);
  }
  return eventId;
}

function showScreen(screenName) {
  document.querySelectorAll('.screen').forEach((screenElement) => {
    screenElement.classList.remove('active');
    screenElement.style.animation = 'none';
  });

  const targetScreen = getElement(`screen-${screenName}`);
  targetScreen.style.animation = '';
  targetScreen.classList.add('active');
  setWaitingSlideChrome(screenName === 'waiting');
  // The navigation sits outside the question screen (a transformed card would
  // pin a fixed bar to itself), so it is shown with that screen only; each
  // question then decides for itself (the finish card hides it).
  const nav = document.querySelector('.q-nav');
  if (nav) nav.hidden = screenName !== 'questions';
}

/**
 * The brand mark, the funder row and the moving background belong to the
 * waiting slide alone - nothing may drift behind a question.
 */
function setWaitingSlideChrome(isWaitingSlide) {
  const funders = getElement('study-funder-logos');
  if (isWaitingSlide) {
    startAmbientBubbles(document.body);
    if (funders && funders.childElementCount) funders.hidden = false;
    return;
  }
  stopAmbientBubbles();
  if (funders) funders.hidden = true;
}

async function applyBranding() {
  void applyFonts();
  const branding = await loadBranding();
  renderGroupLogo(getElement('study-brand-logo'), branding);
  const funders = getElement('study-funder-logos');
  renderFunderLogos(funders, branding);
  // renderFunderLogos reveals the row; keep it hidden unless the slide is up.
  if (funders && !getElement('screen-waiting')?.classList.contains('active')) {
    funders.hidden = true;
  }
}

function shouldStartStudyImmediately() {
  return false;
}

function hasParticipantIdStartCard() {
  const questions = state.config.questions || [];
  return questions[0]?.type === 'participant-id';
}

function hasResolvedParticipantId() {
  const participantId = resolveParticipantId();
  return Boolean(participantId && participantId !== 'unknown');
}

function renderParticipantIdRequiredBlock() {
  const container = getElement('q-container');
  if (container) {
    container.innerHTML = `
      <div class="q-card-study active">
        <div class="q-type-tag"><i class="iconoir-user-badge-check"></i> ${escapeHtml(t('cards.participant.tag', 'Participant ID'))}</div>
        <p class="q-prompt">${escapeHtml(t('study.participantRequiredTitle', 'Participant ID required'))}</p>
        <p class="screen-sub">${escapeHtml(t('study.participantRequiredBody', 'This study cannot start because the first card is not a Participant ID card. Add a Participant ID card as the first card in the admin editor, then reload this tablet page.'))}</p>
      </div>`;
  }
  getElement('btn-prev').disabled = true;
  getElement('btn-next').disabled = true;
  getElement('btn-next-label').textContent = t('study.start', 'Start');
  getElement('btn-next-icon').className = 'iconoir-lock';
  renderCounter(0, 0);
  updateProgressBar(0, 0);
}

async function startTrial(options = {}) {
  if (!Array.isArray(state.config.questions)) {
    showStudyNotice(t('study.configNotReady', 'The study is not ready yet. Please reload the page or tell the study supervisor.'));
    return;
  }

  if (state.startTime) {
    return;
  }
  if (!hasResolvedParticipantId()) {
    showStudyNotice(t('study.participantRequiredAlert', 'Please enter the Participant ID before starting the study.'), 'warning');
    updateNavigation();
    return;
  }

  const rebuild = options.rebuild !== false;
  state.participantIdOverride = resolveParticipantId();
  state.participantMetadataOverride = collectParticipantMetadata();
  state.completedLocally = false;
  state.startTime = Date.now();
  const sessionStarted = await startStudySensorSession();
  if (!sessionStarted) {
    state.startTime = null;
    updateNavigation();
    return;
  }
  saveSessionSnapshot();
  await sendMarker('study_start', null, null, 'study_start');
  if (rebuild) {
    buildQuestions();
  } else {
    const currentQuestion = (state.config.questions || [])[state.currentIndex];
    if (currentQuestion?.type !== 'participant-id') {
      markQuestionShown(state.currentIndex);
      if (currentQuestion?.type === 'stimulus') {
        void startStimulusCard(state.currentIndex, currentQuestion);
      }
    }
    updateNavigation();
  }

  if (!state.config.questions.length) {
    await submitResults();
    return;
  }

  showScreen('questions');
}

function buildQuestions(options = {}) {
  const markInitialShown = options.markInitialShown !== false;
  const startFirstStimulus = options.startFirstStimulus !== false;
  void stopActiveStimulus({ shouldSendStop: false });
  // Cards always render from empty session state; answers already taken for
  // this session (the participant ID) live in the controller's overrides.
  resetAllCardState();

  const container = getElement('q-container');
  container.replaceChildren();
  state.currentIndex = 0;
  state.touchedFields = {};
  state.questionMetrics = {};

  (state.config.questions || []).forEach((question, questionIndex) => {
    const cardModule = CARDS[question.type];
    if (!cardModule) {
      return;
    }

    const cardElement = document.createElement('div');
    cardElement.className = 'q-card-study';
    cardElement.id = `card-q-${questionIndex}`;
    mountCard(cardElement, question, questionIndex, { mode: IS_PREVIEW ? 'preview' : 'study' });
    container.appendChild(cardElement);

  });

  const firstCard = getElement('card-q-0');
  if (firstCard) {
    playCardEntrance(firstCard, 'card-enter-initial');
    if (markInitialShown) {
      markQuestionShown(0);
    }
  }

  updateNavigation();

  const firstQuestion = (state.config.questions || [])[0];
  if (startFirstStimulus && firstQuestion?.type === 'stimulus') {
    void startStimulusCard(0, firstQuestion);
  }
}

function playCardEntrance(cardElement, animationClass) {
  clearCardAnimationClasses(cardElement);
  cardElement.classList.add('active');

  if (!animationClass) {
    return;
  }

  const handleAnimationEnd = (event) => {
    if (event.target !== cardElement) {
      return;
    }
    clearCardAnimationClasses(cardElement);
  };

  cardElement.__cardAnimationEndHandler = handleAnimationEnd;
  cardElement.addEventListener('animationend', handleAnimationEnd);
  window.requestAnimationFrame(() => {
    cardElement.classList.add(animationClass);
  });
}

function clearCardAnimationClasses(cardElement) {
  cardElement.classList.remove('card-enter-initial', 'enter-right', 'enter-left');

  if (cardElement.__cardAnimationEndHandler) {
    cardElement.removeEventListener('animationend', cardElement.__cardAnimationEndHandler);
    cardElement.__cardAnimationEndHandler = null;
  }
}
async function goTo(targetIndex, options = {}) {
  const total = (state.config.questions || []).length;
  if (targetIndex < 0 || targetIndex >= total) {
    return;
  }
  const lockNavigation = options.lockNavigation !== false;
  const force = options.force === true;
  if ((state.navigationBusy || state.submitInFlight) && !force) {
    return;
  }

  if (lockNavigation) {
    state.navigationBusy = true;
    updateNavigation();
  }

  try {
    const shouldSendStop = Boolean(state.activeStimulus?.signalStarted);
    await stopActiveStimulus({ shouldSendStop });

    const currentCard = getElement(`card-q-${state.currentIndex}`);
    const targetCard = getElement(`card-q-${targetIndex}`);
    if (!currentCard || !targetCard) {
      return;
    }

    await recordQuestionCompletion(state.currentIndex);

    const goingForward = targetIndex > state.currentIndex;

    currentCard.classList.remove('active');
    clearCardAnimationClasses(currentCard);
    playCardEntrance(targetCard, goingForward ? 'enter-right' : 'enter-left');

    state.currentIndex = targetIndex;
    markQuestionShown(targetIndex);
    updateNavigation();
    saveSessionSnapshot();
    sendPartialResults();

    const targetQuestion = (state.config.questions || [])[targetIndex];
    if (targetQuestion?.type === 'stimulus') {
      void startStimulusCard(targetIndex, targetQuestion);
    }
  } finally {
    if (lockNavigation) {
      state.navigationBusy = false;
      updateNavigation();
    }
  }
}

const {
  getActiveSeconds,
  getWarmupSeconds,
  prepareStimulusCard,
  startStimulusCard,
  stopActiveStimulus,
} = createParticipantStimulusExecution({
  state,
  getElement,
  createEventId,
  shouldActivateHardware: (...args) => shouldActivateHardware(...args),
  postJson,
  buildEventPayload: (...args) => buildEventPayload(...args),
  estimateServerEpochMs: (...args) => estimateServerEpochMs(...args),
  updateNavigation: (...args) => updateNavigation(...args),
  handleNext: (...args) => handleNext(...args),
  stopStudySensorSession: (...args) => stopStudySensorSession(...args),
  resetParticipantSessionState: (...args) => resetParticipantSessionState(...args),
  showWaitingForAdminStart: (...args) => showWaitingForAdminStart(...args),
  t,
  showStudyNotice: (...args) => showStudyNotice(...args),
  reportNoticeToAdmin: (...args) => reportNoticeToAdmin(...args),
  createModal,
  escapeHtml,
  startDeadlineTimer,
  remainingWholeSeconds,
  participantExtensions,
  getParticipantSessionContext: (...args) => getParticipantSessionContext(...args),
  sendReliableStudyEvent,
  closeVisibilityInterruption: (...args) => closeVisibilityInterruption(...args),
  constants: {
    trialPrepareTimeoutMs: TRIAL_PREPARE_TIMEOUT_MS,
    trialStopTimeoutMs: TRIAL_STOP_TIMEOUT_MS,
    trialStartTimeoutMs: TRIAL_START_TIMEOUT_MS,
  },
});
const {
  collectAnswerEvents,
  collectAnswers,
  collectCardEvents,
  createParticipantExtensionContext,
  getActivePluginActions,
  getParticipantSessionContext,
  getQuestionIndexFromElement,
  getStudyClientHeartbeatPayload,
  handleNext,
  isAnswered,
  isParticipantPluginEnabled,
  markQuestionField,
  markQuestionShown,
  queueParticipantExtensionSync,
  recordQuestionCompletion,
  renderCounter,
  shouldActivateHardware,
  startParticipantExtensionMonitors,
  startStudySensorSession,
  stopStudySensorSession,
  submitResults,
  updateNavigation,
  updateProgressBar,
} = createParticipantResultSubmission({
  state,
  getElement,
  t,
  resolveParticipantId: (...args) => resolveParticipantId(...args),
  collectParticipantMetadata: (...args) => collectParticipantMetadata(...args),
  participantExtensions,
  getClientClockOffsetMs: (...args) => getClientClockOffsetMs(...args),
  estimateServerEpochMs: (...args) => estimateServerEpochMs(...args),
  createEventId,
  sendMarker: (...args) => sendMarker(...args),
  pluginsWithCapability,
  postJson,
  getStudyClientId,
  clearSessionSnapshot: (...args) => clearSessionSnapshot(...args),
  CARDS,
  isAnswerless,
  startTrial: (...args) => startTrial(...args),
  goTo: (...args) => goTo(...args),
  showScreen: (...args) => showScreen(...args),
  showStudyNotice: (...args) => showStudyNotice(...args),
  resetParticipantSessionState: (...args) => resetParticipantSessionState(...args),
  loadPendingSubmission: (...args) => loadPendingSubmission(...args),
  persistPendingSubmission: (...args) => persistPendingSubmission(...args),
  clearPendingSubmission: (...args) => clearPendingSubmission(...args),
  constants: {
    studySessionStopTimeoutMs: STUDY_SESSION_STOP_TIMEOUT_MS,
  },
});
void init();
