import { getJson, postJson } from './api-client.js';
import { startCameraCaptureSession } from './camera-capture.js';
import { CARDS } from './cards/index.js';
import { renderInfoBottom, renderOptionalTag } from './cards/card-info.js';
import { escapeHtml } from './lib/dom-utils.js';
import { onInput as sliderInput } from './cards/card-slider.js';
import { bindDrag as rankBindDrag } from './cards/card-ranking.js';
import { onClick as moodMeterClick } from './cards/card-mood-meter.js';
import { bindCardEvents as bindWordCloudEvents } from './cards/card-word-cloud.js';
import { getStudyClientId, startStudyClientHeartbeat } from './study-client-heartbeat.js';
import { initI18n, t } from './i18n.js';

const state = {
  config: {},
  sensorRuntime: {},
  startTime: null,
  sessionId: '',
  participantIdOverride: '',
  participantMetadataOverride: {},
  currentIndex: 0,
  activeStimulus: null,
  cameraPermission: 'not_requested',
  cameraLastError: '',
  cameraMonitorActive: false,
  clockOffsetMs: null,  // estimated server epoch ms minus tablet performance.now()
  clockRttMs: null,
  touchedFields: {},
  questionMetrics: {},
  sensorSessionStarted: false,
  cameraMonitorCleanup: null,
  cameraMonitorStarting: false,
  runtimePollTimer: null,
  navigationBusy: false,
  submitInFlight: false,
  studyRunState: null,
  waitingForAdminStart: false,
  questionsBuilt: false,
  activationInProgress: false,
  completedLocally: false,
  completedRunId: '',
};

const STUDY_SESSION_STATE_KEY = 'study-runner-active-session';
const RUNTIME_POLL_INTERVAL_MS = 1500;
const CLOCK_SYNC_TIMEOUT_MS = 1000;
const RUNTIME_POLL_TIMEOUT_MS = 1000;
const MARKER_TIMEOUT_MS = 1200;
const TRIAL_START_TIMEOUT_MS = 3000;
const TRIAL_STOP_TIMEOUT_MS = 1500;
const STUDY_SESSION_STOP_TIMEOUT_MS = 1500;
const CAMERA_MONITOR_START_TIMEOUT_MS = 3000;
const CAMERA_CAPTURE_DEFAULT_INTERVAL_MS = 1000;
const CAMERA_CAPTURE_MIN_INTERVAL_MS = 1000;

function getElement(id) {
  return document.getElementById(id);
}

async function init() {
  // A locale failure must not block the study, so swallow errors here.
  try {
    await initI18n();
  } catch (error) {
    console.error('[study] Could not load translations:', error);
  }
  bindEvents();
  initFullscreenUi();
  startStudyClientHeartbeat(getStudyClientHeartbeatPayload, { onHeartbeat: handleHeartbeatResponse });
  bindPageLifecycleEvents();
  // Estimate clock offset in the background; the waiting room must not skip it.
  void syncClock();

  try {
    startRuntimePolling();
    await loadStudyConfig();
    if (!isStudyRunRunning()) {
      showWaitingForAdminStart();
      return;
    }
    await activateStudyUiAfterAdminStart();
  } catch (error) {
    console.error('[study] Could not load configuration:', error);
    showStudyNotice(t('study.loadFailed', 'The study could not be loaded. Please tell the study supervisor.'));
  }

}

async function loadStudyConfig() {
  state.config = await getJson(`/api/config?client_id=${encodeURIComponent(getStudyClientId())}`);
  state.studyRunState = state.config._runtime?.study_run_state || null;
  updateSensorRuntime(state.config._runtime?.sensor_runtime || {});
}

function isStudyRunRunning(runState = state.studyRunState) {
  return runState?.status === 'running';
}

function showWaitingForAdminStart(options = {}) {
  state.completedLocally = false;
  state.waitingForAdminStart = true;
  state.questionsBuilt = false;
  const title = getElement('study-waiting-title');
  const body = getElement('study-waiting-body');
  if (title) {
    title.textContent = options.title || t('study.waiting.title', 'Study will start soon');
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
  state.activationInProgress = true;
  try {
    await loadStudyConfig();
    if (!isStudyRunRunning()) {
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
    buildQuestions({ markInitialShown: false, startFirstStimulus: false });
    state.questionsBuilt = true;
    state.waitingForAdminStart = false;
    showScreen('questions');
    const recoveryVisible = renderRecoveryBlockIfNeeded();
    if (!recoveryVisible) {
      void startCameraMonitorIfNeeded();
    }
    if (shouldStartStudyImmediately()) {
      void startTrial({ rebuild: false });
    }
    void requestStudyFullscreen();
  } finally {
    state.activationInProgress = false;
  }
}

function handleStudyRunState(runState) {
  if (!runState || typeof runState !== 'object') {
    return;
  }
  const previousRunId = state.studyRunState?.run_id || '';
  const nextRunId = runState.run_id || '';
  if (previousRunId && nextRunId && previousRunId !== nextRunId) {
    state.completedLocally = false;
    state.completedRunId = '';
    state.questionsBuilt = false;
    state.startTime = null;
    state.sessionId = '';
    state.sensorSessionStarted = false;
  }
  state.studyRunState = runState;
  if (state.completedLocally && runState.status === 'loaded') {
    state.completedLocally = false;
    state.completedRunId = '';
    state.questionsBuilt = false;
    state.startTime = null;
    state.sessionId = '';
    state.sensorSessionStarted = false;
    showWaitingForAdminStart();
    return;
  }
  if (state.completedLocally && runState.status === 'running' && nextRunId !== state.completedRunId) {
    state.completedLocally = false;
    state.completedRunId = '';
    state.questionsBuilt = false;
    state.startTime = null;
    state.sessionId = '';
    state.sensorSessionStarted = false;
  }
  if (runState.conflict === true || runState.status === 'blocked') {
    showWaitingForAdminStart({
      title: t('study.tabletConflict.title', 'This tablet is not assigned'),
      body: runState.message || t('study.tabletConflict.body', 'Another tablet is already assigned to this study run. Please tell the study supervisor.'),
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
async function syncClock() {
  const ROUNDS = 3;
  const offsets = [];
  const rtts = [];

  for (let i = 0; i < ROUNDS; i++) {
    const clientSendMs = performance.now();
    try {
      const resp = await postJson('/api/sync-clock', {
        client_id: getStudyClientId(),
        client_send_ms: clientSendMs,
      }, { timeoutMs: CLOCK_SYNC_TIMEOUT_MS });
      const clientRecvMs = performance.now();
      const srvRecv = resp.server_receive_ms;
      const srvSend = resp.server_send_ms;
      const offset = ((srvRecv - clientSendMs) + (srvSend - clientRecvMs)) / 2;
      offsets.push(offset);
      rtts.push(Math.max(0, clientRecvMs - clientSendMs));
    } catch {
      // Server unreachable; skip this round.
    }
    // Small delay between rounds to avoid burst
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  if (offsets.length > 0) {
    offsets.sort((a, b) => a - b);
    state.clockOffsetMs = offsets[Math.floor(offsets.length / 2)];
    rtts.sort((a, b) => a - b);
    const medianRtt = rtts[Math.floor(rtts.length / 2)];
    state.clockRttMs = Number.isFinite(medianRtt) ? medianRtt : null;
    console.debug('[study] Clock offset estimated:', state.clockOffsetMs.toFixed(2), 'ms');
  }
}

function estimateServerEpochMs(clientPerfMs = performance.now()) {
  if (Number.isFinite(state.clockOffsetMs)) {
    return clientPerfMs + state.clockOffsetMs;
  }
  return Date.now();
}

let _studyNoticeTimer = null;
function showStudyNotice(message, type = 'error', durationMs = 6000) {
  // In-page notice instead of a blocking browser popup on the tablet.
  let toast = document.getElementById('study-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'study-toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = `toast toast--${type} show`;
  clearTimeout(_studyNoticeTimer);
  _studyNoticeTimer = setTimeout(() => toast.classList.remove('show'), durationMs);
}

function getClientClockOffsetMs() {
  // Difference between the server clock and this tablet's wall clock
  // (server_epoch = client_epoch + offset). Null when clock sync failed.
  if (!Number.isFinite(state.clockOffsetMs)) {
    return null;
  }
  return Math.round(estimateServerEpochMs() - Date.now());
}

function handleHeartbeatResponse(response) {
  if (response?.sensor_runtime) {
    updateSensorRuntime(response.sensor_runtime);
  }
  if (response?.study_run_state) {
    handleStudyRunState(response.study_run_state);
  }
}

function updateSensorRuntime(sensorRuntime) {
  const previousCameraEnabled = isStudySensorEnabled('camera_emotion');
  state.sensorRuntime = sensorRuntime && typeof sensorRuntime === 'object' ? sensorRuntime : {};
  const nextCameraEnabled = isStudySensorEnabled('camera_emotion');
  const cameraMayRun = Boolean(state.startTime) || (isStudyRunRunning() && state.questionsBuilt);
  if (nextCameraEnabled && cameraMayRun && !state.cameraMonitorCleanup && !state.cameraMonitorStarting) {
    void startCameraMonitorIfNeeded();
  }
  if (!nextCameraEnabled && previousCameraEnabled && state.cameraMonitorCleanup) {
    stopCameraMonitor();
  }
}

function startRuntimePolling() {
  if (state.runtimePollTimer !== null) {
    window.clearInterval(state.runtimePollTimer);
  }
  const poll = async () => {
    try {
      const runtime = await getJson(`/api/study/runtime?client_id=${encodeURIComponent(getStudyClientId())}`, { timeoutMs: RUNTIME_POLL_TIMEOUT_MS });
      updateSensorRuntime(runtime?.sensor_runtime || {});
      handleStudyRunState(runtime?.study_run_state);
    } catch (error) {
      console.debug('[study] Runtime poll failed:', error);
    }
  };
  void poll();
  state.runtimePollTimer = window.setInterval(poll, RUNTIME_POLL_INTERVAL_MS);
}

function bindPageLifecycleEvents() {
  const sendLeaveEvent = () => {
    saveSessionSnapshot();
    sendPartialResults({ useBeacon: true });
    const payload = {
      event: 'client_reload_or_leave',
      ...getSessionPayload(),
      current_index: state.currentIndex,
      current_type: (state.config.questions || [])[state.currentIndex]?.type || null,
      is_stimulus_active: Boolean(state.activeStimulus),
    };
    try {
      const body = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/study/session/client-event', new Blob([body], { type: 'application/json' }));
        return;
      }
    } catch {
      // Fall through to fetch.
    }
    void postJson('/api/study/session/client-event', payload).catch(() => {});
  };
  window.addEventListener('pagehide', sendLeaveEvent);
  window.addEventListener('beforeunload', sendLeaveEvent);
}

function getSessionPayload() {
  return {
    session_id: state.sessionId,
    client_id: getStudyClientId(),
    study_id: state.config.study_id || '',
    participant_id: resolveParticipantId(),
  };
}

function saveSessionSnapshot() {
  if (!state.startTime || !state.sessionId) {
    return;
  }
  try {
    window.sessionStorage.setItem(STUDY_SESSION_STATE_KEY, JSON.stringify({
      session_id: state.sessionId,
      client_id: getStudyClientId(),
      study_id: state.config.study_id || '',
      participant_id: resolveParticipantId(),
      participant_metadata: collectParticipantMetadata(),
      current_index: state.currentIndex,
      current_type: (state.config.questions || [])[state.currentIndex]?.type || '',
      study_started_at: new Date(state.startTime).toISOString(),
      sensor_session_started: state.sensorSessionStarted,
    }));
  } catch {
    // Session recovery is best-effort.
  }
}

function buildPartialResultsPayload() {
  return {
    ...getSessionPayload(),
    client_clock_offset_ms: getClientClockOffsetMs(),
    timestamp_start: state.startTime ? new Date(state.startTime).toISOString() : null,
    snapshot_at: new Date().toISOString(),
    current_index: state.currentIndex,
    answers: collectAnswers(),
    participant_metadata: collectParticipantMetadata(),
    answer_events: collectAnswerEvents(),
    card_events: collectCardEvents(),
  };
}

function sendPartialResults({ useBeacon = false } = {}) {
  // Server-side safety copy of everything answered so far. Without it,
  // closing the tab (sessionStorage is per-tab) loses the whole session.
  if (!state.startTime || !state.sessionId) {
    return;
  }
  try {
    const payload = buildPartialResultsPayload();
    if (useBeacon && navigator.sendBeacon) {
      navigator.sendBeacon('/api/results/partial', new Blob([JSON.stringify(payload)], { type: 'application/json' }));
      return;
    }
    void postJson('/api/results/partial', payload, { timeoutMs: 1500 }).catch(() => {});
  } catch {
    // Partial saves are best-effort; the final submit is authoritative.
  }
}

function loadSessionSnapshot() {
  try {
    const raw = window.sessionStorage.getItem(STUDY_SESSION_STATE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function clearSessionSnapshot() {
  try {
    window.sessionStorage.removeItem(STUDY_SESSION_STATE_KEY);
  } catch {
    // Ignore storage failures.
  }
}

function renderRecoveryBlockIfNeeded() {
  const snapshot = loadSessionSnapshot();
  if (!snapshot || snapshot.study_id !== (state.config.study_id || '') || snapshot.client_id !== getStudyClientId()) {
    return false;
  }

  const container = getElement('q-container');
  if (!container) {
    return false;
  }
  container.innerHTML = `
    <div class="q-card-study active">
      <div class="q-type-tag"><i class="iconoir-refresh"></i> ${escapeHtml(t('study.recoveryTag', 'Session recovery'))}</div>
      <p class="q-prompt">${escapeHtml(t('study.recoveryTitle', 'Study page was reloaded'))}</p>
      <p class="screen-sub">${escapeHtml(t('study.recoveryBody', 'A running study session was found for this tablet. Continue only if this was an accidental reload. Active stimulus timing is marked as interrupted.'))}</p>
      <div class="dashboard-actions">
        <button class="btn-secondary" type="button" id="btn-recover-session">${escapeHtml(t('study.recoveryContinue', 'Continue study'))}</button>
        <button class="btn-secondary" type="button" id="btn-recover-discard">${escapeHtml(t('study.recoveryRestart', 'Start over'))}</button>
      </div>
    </div>`;
  getElement('btn-prev').disabled = true;
  getElement('btn-next').disabled = true;
  getElement('btn-next-label').textContent = t('study.next', 'Next');
  getElement('btn-next-icon').className = 'iconoir-lock';
  getElement('btn-recover-session')?.addEventListener('click', () => void resumeAfterReload(snapshot));
  getElement('btn-recover-discard')?.addEventListener('click', () => {
    clearSessionSnapshot();
    buildQuestions({ markInitialShown: false, startFirstStimulus: false });
  });
  return true;
}

async function resumeAfterReload(snapshot) {
  try {
    const response = await postJson('/api/study/session/resume', {
      event: 'study_resume_after_reload',
      session_id: snapshot.session_id,
      client_id: getStudyClientId(),
      study_id: snapshot.study_id,
      participant_id: snapshot.participant_id,
      current_index: snapshot.current_index,
      current_type: snapshot.current_type,
    });
    state.sessionId = response.session?.session_id || snapshot.session_id || '';
    state.participantIdOverride = snapshot.participant_id || '';
    state.participantMetadataOverride = snapshot.participant_metadata || {};
    state.startTime = Date.parse(snapshot.study_started_at) || Date.now();
    state.sensorSessionStarted = Boolean(snapshot.sensor_session_started);
    updateSensorRuntime(response.sensor_runtime || state.sensorRuntime);
    buildQuestions({ markInitialShown: false, startFirstStimulus: false });
    const targetIndex = Number.isInteger(Number(snapshot.current_index)) ? Number(snapshot.current_index) : 0;
    const safeIndex = Math.max(0, Math.min(targetIndex, (state.config.questions || []).length - 1));
    if (safeIndex === 0) {
      markQuestionShown(0);
      updateNavigation();
    } else {
      showRecoveredCard(safeIndex);
    }
    saveSessionSnapshot();
    void startCameraMonitorIfNeeded();
  } catch (error) {
    console.error('[study] Could not resume study session:', error);
    showStudyNotice(t('study.recoveryFailed', 'Could not resume the study session.'));
  }
}

function showRecoveredCard(targetIndex) {
  const currentCard = getElement(`card-q-${state.currentIndex}`);
  const targetCard = getElement(`card-q-${targetIndex}`);
  if (!targetCard) {
    return;
  }
  if (currentCard && currentCard !== targetCard) {
    currentCard.classList.remove('active');
  }
  playCardEntrance(targetCard, 'card-enter-initial');
  state.currentIndex = targetIndex;
  markQuestionShown(targetIndex);
  const targetQuestion = (state.config.questions || [])[targetIndex];
  if (targetQuestion?.type === 'stimulus') {
    prepareStimulusCard(targetIndex, targetQuestion);
  }
  updateNavigation();
}

function bindEvents() {
  getElement('btn-prev').addEventListener('click', () => void goTo(state.currentIndex - 1));
  getElement('btn-next').addEventListener('click', () => void handleNext());
  getElement('btn-study-fullscreen')?.addEventListener('click', () => void toggleStudyFullscreen());

  const questionContainer = getElement('q-container');
  questionContainer.addEventListener('input', handleQuestionInput);
  questionContainer.addEventListener('click', (event) => moodMeterClick(event));
  questionContainer.addEventListener('change', handleQuestionChange);
  questionContainer.addEventListener('ranking:changed', handleQuestionChange);
  questionContainer.addEventListener('wordcloud:changed', handleQuestionChange);
  questionContainer.addEventListener('moodmeter:changed', handleQuestionChange);
  questionContainer.addEventListener('participantid:changed', handleQuestionChange);
}

function initFullscreenUi() {
  const fullscreenUi = getElement('study-fullscreen-ui');
  if (!fullscreenUi || !isFullscreenSupported()) {
    return;
  }

  fullscreenUi.hidden = false;
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

  fullscreenUi.hidden = false;
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

function handleQuestionInput(event) {
  const target = event.target;
  const questionIndex = getQuestionIndexFromElement(target);
  if (questionIndex !== null && target?.matches('.js-slider-input')) {
    markQuestionField(questionIndex, target.id || target.name || 'slider');
  }
  if (questionIndex !== null && target?.matches('textarea.fi-textarea')) {
    markQuestionField(questionIndex, target.id || 'text');
  }

  sliderInput(event);
  
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
  if (questionIndex !== null && event.type === 'ranking:changed') {
    markQuestionField(questionIndex, 'ranking');
  }
  if (questionIndex !== null && (event.type === 'wordcloud:changed' || event.type === 'moodmeter:changed')) {
    markQuestionField(questionIndex, 'selection');
  }
  
  if (event.type !== 'participantid:changed') {
    CARDS['participant-id']?.onInput(event);
  }
  updateNavigation();
  saveSessionSnapshot();
}

function resolveParticipantId() {
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
    participant_id: resolveParticipantId(),
    question_index: Number.isInteger(questionIndex) ? questionIndex : null,
    question_type: question?.type || '',
    phase,
    client_trigger_ms: clientTriggerMs,
    client_trigger_epoch_ms: estimateServerEpochMs(clientTriggerMs),
    clock_offset_ms: getClientClockOffsetMs(),
  };
}

async function sendMarker(markerEvent, questionIndex, question, phase = markerEvent) {
  try {
    const payload = buildEventPayload(questionIndex, question, phase);
    await postJson('/api/marker', {
      ...payload,
      marker_event: markerEvent,
      send_signal: true,
      brainbit_to_lsl: false,
      brainbit_to_touchdesigner: false,
      mini_radar_recording_enabled: false,
    }, { timeoutMs: MARKER_TIMEOUT_MS });
  } catch (error) {
    console.error('[study] Could not send /api/marker:', error);
  }
}

function showScreen(screenName) {
  document.querySelectorAll('.screen').forEach((screenElement) => {
    screenElement.classList.remove('active');
    screenElement.style.animation = 'none';
  });

  const targetScreen = getElement(`screen-${screenName}`);
  targetScreen.style.animation = '';
  targetScreen.classList.add('active');
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
    cardElement.innerHTML = renderOptionalTag(question) + cardModule.renderStudy(question, questionIndex) + renderInfoBottom(question);
    container.appendChild(cardElement);

    if (question.type === 'ranking') {
      const rankList = cardElement.querySelector('.rank-list');
      if (rankList) rankBindDrag(rankList);
    }
    if (question.type === 'word-cloud') {
      bindWordCloudEvents(cardElement, questionIndex);
    }
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

async function startStimulusCard(questionIndex, question) {
  const stimulusRun = {
    index: questionIndex,
    question,
    timerId: null,
    signalStarted: false,
    cleanup: null,
  };

  state.activeStimulus = stimulusRun;
  prepareStimulusCard(questionIndex, question);

  if (getWarmupSeconds(question) > 0) {
    startWarmupPhase(stimulusRun);
    return;
  }

  await startActiveStimulusPhase(stimulusRun);
}

function startWarmupPhase(stimulusRun) {
  const { index, question } = stimulusRun;
  const totalSeconds = getWarmupSeconds(question);
  const numberLabel = getElement(`warmup-num-${index}`);
  let elapsedSeconds = 0;

  setStimulusPhase(index, 'warmup');
  updateNavigation();

  if (numberLabel) {
    numberLabel.textContent = String(totalSeconds);
  }

  stimulusRun.timerId = window.setInterval(() => {
    if (state.activeStimulus !== stimulusRun) {
      return;
    }

    elapsedSeconds += 1;
    if (numberLabel) {
      numberLabel.textContent = String(Math.max(0, totalSeconds - elapsedSeconds));
    }

    if (elapsedSeconds >= totalSeconds) {
      clearInterval(stimulusRun.timerId);
      stimulusRun.timerId = null;
      void startActiveStimulusPhase(stimulusRun);
    }
  }, 1000);
}

async function startActiveStimulusPhase(stimulusRun) {
  if (state.activeStimulus !== stimulusRun) {
    return;
  }

  const { index, question } = stimulusRun;
  const totalSeconds = getActiveSeconds(question);
  const ring = getElement(`ring-prog-${index}`);
  const numberLabel = getElement(`cd-num-${index}`);
  let elapsedSeconds = 0;

  setStimulusPhase(index, 'active');

  if (shouldActivateHardware(question)) {
    try {
      const clientTriggerMs = performance.now();
      const currentMetrics = state.questionMetrics[index] || {};
      state.questionMetrics[index] = {
        ...currentMetrics,
        active_started_at: new Date().toISOString(),
        client_start_trigger_epoch_ms: estimateServerEpochMs(clientTriggerMs),
      };
      const response = await postJson('/api/start', {
        ...buildEventPayload(index, question, 'stimulus_active_start', clientTriggerMs),
        marker_event: 'stimulus_active_start',
        send_signal: question.send_signal !== false,
        brainbit_to_lsl: isStudySensorEnabled('brainbit') && question.brainbit_to_lsl !== false,
        brainbit_to_touchdesigner: isStudySensorEnabled('brainbit') && question.brainbit_to_touchdesigner !== false,
        mini_radar_recording_enabled: isStudySensorEnabled('mini_radar') && question.mini_radar_recording_enabled !== false,
      }, { timeoutMs: TRIAL_START_TIMEOUT_MS });
      state.questionMetrics[index] = {
        ...state.questionMetrics[index],
        server_start_received_at: response.server_received_at || null,
        server_start_received_epoch_ms: response.server_received_epoch_ms || null,
        start_marker: response.marker_value || null,
      };
      stimulusRun.signalStarted = true;
    } catch (error) {
      console.error('[study] Could not send /api/start:', error);
    }
  }

  const contentCleanup = applyStimulusContent(index, question);
  const cameraCleanup = await maybeStartCameraCapture(stimulusRun);
  stimulusRun.cleanup = () => {
    if (typeof cameraCleanup === 'function') {
      cameraCleanup();
    }
    if (typeof contentCleanup === 'function') {
      contentCleanup();
    }
  };
  updateNavigation();

  if (numberLabel) {
    numberLabel.textContent = String(totalSeconds);
  }
  if (ring) {
    ring.style.strokeDashoffset = '0';
  }

  stimulusRun.timerId = window.setInterval(() => {
    if (state.activeStimulus !== stimulusRun) {
      return;
    }

    elapsedSeconds += 1;

    if (numberLabel) {
      numberLabel.textContent = String(Math.max(0, totalSeconds - elapsedSeconds));
    }
    if (ring) {
      ring.style.strokeDashoffset = String(314 * (elapsedSeconds / totalSeconds));
    }

    if (elapsedSeconds >= totalSeconds) {
      clearInterval(stimulusRun.timerId);
      stimulusRun.timerId = null;
      void finishStimulusCard(stimulusRun);
    }
  }, 1000);
}

async function finishStimulusCard(stimulusRun) {
  if (state.activeStimulus !== stimulusRun) {
    return;
  }

  await stopActiveStimulus({ shouldSendStop: stimulusRun.signalStarted });
  await handleNext();
}

async function maybeStartCameraCapture(stimulusRun) {
  const { index, question } = stimulusRun;
  if (question.camera_capture_enabled !== true || !isStudySensorEnabled('camera_emotion')) {
    return null;
  }
  if (typeof state.cameraMonitorCleanup === 'function') {
    return null;
  }

  return startCameraCaptureSession({
    intervalMs: Math.max(CAMERA_CAPTURE_MIN_INTERVAL_MS, Number(question.camera_snapshot_interval_ms || CAMERA_CAPTURE_DEFAULT_INTERVAL_MS)),
    getPayload: () => ({
      participant_id: resolveParticipantId(),
      study_id: state.config.study_id || '',
      question_index: index,
      question_type: question.type,
    }),
    onState: (cameraState) => {
      state.cameraPermission = cameraState.permission || state.cameraPermission;
      if (cameraState.permission !== 'granted' && cameraState.permission !== 'stopped') {
        console.warn('[camera]', cameraState.message || cameraState.permission);
      }
    },
  });
}

async function startCameraMonitorIfNeeded() {
  if (!Array.isArray(state.config.questions) || !isStudySensorEnabled('camera_emotion') || state.cameraMonitorStarting || typeof state.cameraMonitorCleanup === 'function') {
    return;
  }

  state.cameraMonitorStarting = true;
  try {
    await postJson('/api/study/camera-monitor/start', {
      study_id: state.config.study_id || '',
    }, { timeoutMs: CAMERA_MONITOR_START_TIMEOUT_MS });
    const cleanup = await startCameraCaptureSession({
      intervalMs: getCameraMonitorIntervalMs(),
      preview: true,
      activePhase: false,
      getFrameState: getCameraFrameState,
      getPayload: getCameraMonitorPayload,
      onState: (cameraState) => {
        state.cameraPermission = cameraState.permission || state.cameraPermission;
        state.cameraMonitorActive = ['granted', 'uploading'].includes(cameraState.permission);
        state.cameraLastError = state.cameraMonitorActive || cameraState.permission === 'stopped'
          ? ''
          : (cameraState.message || state.cameraLastError || '');
        if (!['granted', 'uploading', 'stopped'].includes(cameraState.permission)) {
          console.warn('[camera]', cameraState.message || cameraState.permission);
        }
      },
    });
    if (typeof cleanup === 'function') {
      state.cameraMonitorCleanup = cleanup;
    }
  } catch (error) {
    console.warn('[camera] Could not start camera monitor:', error);
  } finally {
    state.cameraMonitorStarting = false;
  }
}

function stopCameraMonitor() {
  if (typeof state.cameraMonitorCleanup !== 'function') {
    return;
  }
  try {
    state.cameraMonitorCleanup();
  } catch (error) {
    console.warn('[camera] Could not stop camera monitor:', error);
  } finally {
    state.cameraMonitorCleanup = null;
    state.cameraMonitorActive = false;
  }
}

function getCameraFrameState() {
  const activeQuestion = state.activeStimulus?.question || null;
  const activeCameraSample = Boolean(
    state.startTime
    && state.activeStimulus
    && activeQuestion?.camera_capture_enabled === true
    && isStudySensorEnabled('camera_emotion')
  );
  return {
    preview: !activeCameraSample,
    activePhase: activeCameraSample,
  };
}

function getCameraMonitorPayload() {
  const questions = state.config.questions || [];
  const activeQuestion = state.activeStimulus?.question || null;
  const activeCameraSample = !getCameraFrameState().preview;
  const questionIndex = activeCameraSample ? state.activeStimulus.index : null;
  const question = activeCameraSample ? activeQuestion : questions[state.currentIndex] || null;
  return {
    participant_id: resolveParticipantId(),
    study_id: state.config.study_id || '',
    question_index: questionIndex,
    question_type: activeCameraSample ? question?.type || '' : 'prestudy_monitor',
    phase: activeCameraSample ? 'stimulus_active' : 'prestudy_monitor',
  };
}

function getCameraMonitorIntervalMs() {
  const questions = state.config.questions || [];
  const cameraStimulus = questions.find((question) => (
    question?.type === 'stimulus'
    && question.camera_capture_enabled === true
    && Number.isFinite(Number(question.camera_snapshot_interval_ms))
  ));
  return Math.max(
    CAMERA_CAPTURE_MIN_INTERVAL_MS,
    cameraStimulus ? Number(cameraStimulus.camera_snapshot_interval_ms) : CAMERA_CAPTURE_DEFAULT_INTERVAL_MS,
  );
}

async function stopActiveStimulus({ shouldSendStop }) {
  const stimulusRun = state.activeStimulus;
  if (!stimulusRun) {
    return;
  }

  if (stimulusRun.timerId) {
    clearInterval(stimulusRun.timerId);
    stimulusRun.timerId = null;
  }

  if (typeof stimulusRun.cleanup === 'function') {
    try {
      stimulusRun.cleanup();
    } catch (error) {
      console.error('[stimulus] Cleanup callback failed:', error);
    }
  }

  clearStimulusContent(stimulusRun.index);

  if (shouldSendStop && stimulusRun.signalStarted && shouldActivateHardware(stimulusRun.question)) {
    try {
      const clientTriggerMs = performance.now();
      const currentMetrics = state.questionMetrics[stimulusRun.index] || {};
      state.questionMetrics[stimulusRun.index] = {
        ...currentMetrics,
        active_ended_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        client_stop_trigger_epoch_ms: estimateServerEpochMs(clientTriggerMs),
      };
      const response = await postJson('/api/stop', {
        ...buildEventPayload(stimulusRun.index, stimulusRun.question, 'stimulus_active_stop', clientTriggerMs),
        marker_event: 'stimulus_active_stop',
        send_signal: stimulusRun.question.send_signal !== false,
        brainbit_to_lsl: isStudySensorEnabled('brainbit') && stimulusRun.question.brainbit_to_lsl !== false,
        brainbit_to_touchdesigner: isStudySensorEnabled('brainbit') && stimulusRun.question.brainbit_to_touchdesigner !== false,
        mini_radar_recording_enabled: false,
      }, { timeoutMs: TRIAL_STOP_TIMEOUT_MS });
      state.questionMetrics[stimulusRun.index] = {
        ...state.questionMetrics[stimulusRun.index],
        server_stop_received_at: response.server_received_at || null,
        server_stop_received_epoch_ms: response.server_received_epoch_ms || null,
        stop_marker: response.marker_value || null,
      };
    } catch (error) {
      console.error('[study] Could not send /api/stop:', error);
    }
  } else if (stimulusRun.question?.type === 'stimulus') {
    const currentMetrics = state.questionMetrics[stimulusRun.index] || {};
    state.questionMetrics[stimulusRun.index] = {
      ...currentMetrics,
      completed_at: currentMetrics.completed_at || new Date().toISOString(),
    };
  }

  prepareStimulusCard(stimulusRun.index, stimulusRun.question);
  state.activeStimulus = null;
  updateNavigation();
}

function prepareStimulusCard(questionIndex, question) {
  const shell = getElement(`stimulus-shell-${questionIndex}`);
  const warmupLabel = getElement(`warmup-num-${questionIndex}`);
  const activeLabel = getElement(`cd-num-${questionIndex}`);
  const ring = getElement(`ring-prog-${questionIndex}`);

  if (shell) {
    shell.classList.remove('stimulus-body--warmup', 'stimulus-body--active');
    shell.classList.add(getWarmupSeconds(question) > 0 ? 'stimulus-body--warmup' : 'stimulus-body--active');
  }

  if (warmupLabel) {
    warmupLabel.textContent = String(getWarmupSeconds(question));
  }
  if (activeLabel) {
    activeLabel.textContent = String(getActiveSeconds(question));
  }
  if (ring) {
    ring.style.strokeDashoffset = '0';
  }

  clearStimulusContent(questionIndex);
  setStimulusPhase(questionIndex, getWarmupSeconds(question) > 0 ? 'warmup' : 'active');
}

function setStimulusPhase(questionIndex, phase) {
  const shell = getElement(`stimulus-shell-${questionIndex}`);
  const warmupStage = getElement(`stimulus-warmup-${questionIndex}`);
  const activeStage = getElement(`stimulus-active-${questionIndex}`);

  if (shell) {
    shell.dataset.phase = phase;
    shell.classList.toggle('stimulus-body--warmup', phase === 'warmup');
    shell.classList.toggle('stimulus-body--active', phase === 'active');
  }
  if (warmupStage) {
    warmupStage.hidden = phase !== 'warmup';
  }
  if (activeStage) {
    activeStage.hidden = phase !== 'active';
  }
}

function clearStimulusContent(questionIndex) {
  const contentElement = getElement(`stimulus-content-${questionIndex}`);
  if (!contentElement) {
    return;
  }

  contentElement.querySelectorAll('video, audio').forEach((mediaElement) => {
    try {
      mediaElement.pause();
      mediaElement.removeAttribute('src');
      if (typeof mediaElement.load === 'function') {
        mediaElement.load();
      }
    } catch (error) {
      console.error('[stimulus] Could not stop media element:', error);
    }
  });

  contentElement.replaceChildren();
  contentElement.hidden = true;
}

function isUnsafeStimulusCodeAllowed() {
  return state.config?._capabilities?.unsafe_stimulus_code === true;
}

function showUnsafeStimulusWarning(contentElement, triggerType) {
  const warningBox = document.createElement('div');
  warningBox.className = 'stimulus-unsafe-warning';

  const title = document.createElement('strong');
  title.textContent = `${String(triggerType).toUpperCase()} stimulus blocked`;

  const message = document.createElement('p');
  message.textContent = 'This study uses executable stimulus content, but the server has not enabled unsafe stimulus code. Set STUDY_RUNNER_ALLOW_UNSAFE_STIMULUS_CODE=1 on the server to allow it intentionally.';

  warningBox.appendChild(title);
  warningBox.appendChild(message);
  contentElement.appendChild(warningBox);
  contentElement.hidden = false;
}

function applyStimulusContent(questionIndex, question) {
  const contentElement = getElement(`stimulus-content-${questionIndex}`);
  if (!contentElement) {
    return null;
  }

  clearStimulusContent(questionIndex);

  const cleanupCallbacks = [];
  const triggerType = question.trigger_type || 'timer';
  const triggerContent = question.trigger_content || '';

  if (triggerType === 'image' && triggerContent) {
    const image = document.createElement('img');
    image.src = triggerContent;
    image.className = 'stimulus-image';
    image.alt = '';
    contentElement.appendChild(image);
    contentElement.hidden = false;
  } else if (triggerType === 'video' && triggerContent) {
    const video = document.createElement('video');
    video.src = triggerContent;
    video.className = 'stimulus-video';
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    video.playsInline = true;
    contentElement.appendChild(video);
    contentElement.hidden = false;
  } else if (triggerType === 'audio' && triggerContent) {
    const audio = document.createElement('audio');
    audio.src = triggerContent;
    audio.autoplay = true;
    audio.loop = true;
    contentElement.appendChild(audio);
  } else if (triggerType === 'html' && triggerContent) {
    if (!isUnsafeStimulusCodeAllowed()) {
      showUnsafeStimulusWarning(contentElement, triggerType);
    } else {
      contentElement.innerHTML = triggerContent;
      contentElement.hidden = false;
    }
  } else if (triggerType === 'js' && triggerContent) {
    if (!isUnsafeStimulusCodeAllowed()) {
      showUnsafeStimulusWarning(contentElement, triggerType);
      return null;
    }

    const studyHelper = {
      call: (path, data = {}) => postJson(path, data),
      onCleanup: (callback) => {
        if (typeof callback === 'function') {
          cleanupCallbacks.push(callback);
        }
      },
    };

    try {
      const returnedCleanup = (new Function('study', triggerContent))(studyHelper);
      if (typeof returnedCleanup === 'function') {
        cleanupCallbacks.push(returnedCleanup);
      }
    } catch (error) {
      console.error('[stimulus] Custom JavaScript error:', error);
    }
  }

  return () => {
    cleanupCallbacks.forEach((callback) => {
      try {
        callback();
      } catch (error) {
        console.error('[stimulus] Custom cleanup failed:', error);
      }
    });
  };
}

function getWarmupSeconds(question) {
  return Math.max(0, Math.round((question.warmup_duration_ms || 0) / 1000));
}

function getActiveSeconds(question) {
  return Math.max(1, Math.round((question.duration_ms || 30000) / 1000));
}

function getStudyClientHeartbeatPayload() {
  const questions = state.config.questions || [];
  const currentQuestion = questions[state.currentIndex] || null;

  return {
    participant_id: resolveParticipantId(),
    study_id: state.config.study_id || '',
    session_id: state.sessionId,
    current_index: Number.isInteger(state.currentIndex) ? state.currentIndex : null,
    current_type: currentQuestion?.type || null,
    is_stimulus_active: Boolean(state.activeStimulus),
    signal_started: Boolean(state.activeStimulus?.signalStarted),
    camera_permission: state.cameraPermission,
    camera_monitor_requested: isStudySensorEnabled('camera_emotion'),
    camera_monitor_active: state.cameraMonitorActive,
    camera_last_error: state.cameraLastError,
    study_started: Boolean(state.startTime),
    study_run_status: state.studyRunState?.status || 'loaded',
    waiting_for_admin_start: Boolean(state.waitingForAdminStart),
    clock_offset_ms: getClientClockOffsetMs(),
    clock_sync_rtt_ms: Number.isFinite(state.clockRttMs) ? Math.round(state.clockRttMs) : null,
  };
}

function getQuestionIndexFromElement(element) {
  const cardElement = element?.closest?.('.q-card-study');
  if (!cardElement?.id?.startsWith('card-q-')) {
    return null;
  }

  const index = Number.parseInt(cardElement.id.replace('card-q-', ''), 10);
  return Number.isInteger(index) ? index : null;
}

function markQuestionField(questionIndex, fieldKey) {
  const normalizedKey = fieldKey || '__question__';
  if (!state.touchedFields[questionIndex]) {
    state.touchedFields[questionIndex] = new Set();
  }
  state.touchedFields[questionIndex].add(normalizedKey);
}

function markQuestionShown(questionIndex) {
  const questions = state.config.questions || [];
  const question = questions[questionIndex];
  const nowIso = new Date().toISOString();
  const current = state.questionMetrics[questionIndex] || {};
  state.questionMetrics[questionIndex] = {
    ...current,
    shown_at: current.shown_at || nowIso,
    // Server-clock estimate so biosignal slicing is immune to tablet clock skew.
    shown_at_server_epoch_ms: current.shown_at_server_epoch_ms || estimateServerEpochMs(),
  };
  if (!state.startTime || question?.type === 'participant-id') {
    return;
  }
  if (question?.type !== 'finish' && !current.shown_marker_sent) {
    state.questionMetrics[questionIndex].shown_marker_sent = true;
    void sendMarker(
      question?.type === 'stimulus' ? 'stimulus_shown' : 'question_shown',
      questionIndex,
      question,
      'shown',
    );
  }
}

function recordQuestionCompletion(questionIndex) {
  const questions = state.config.questions || [];
  const question = questions[questionIndex];
  if (!question || question.type === 'stimulus' || question.type === 'finish') {
    return Promise.resolve();
  }

  const current = state.questionMetrics[questionIndex] || {};
  if (current.answered_at) {
    return Promise.resolve();
  }
  state.questionMetrics[questionIndex] = {
    ...current,
    answered_at: new Date().toISOString(),
    answered_at_server_epoch_ms: estimateServerEpochMs(),
  };
  if (!state.startTime || question.type === 'participant-id') {
    return Promise.resolve();
  }
  return sendMarker('question_answered', questionIndex, question, 'answered');
}

function getTouchedFieldCount(questionIndex) {
  return state.touchedFields[questionIndex]?.size || 0;
}

function shouldActivateHardware(question) {
  if (state.config.study_settings && state.config.study_settings.sensors_enabled === false) {
    return false;
  }
  const brainbitRequested = isStudySensorEnabled('brainbit') && (
    question.brainbit_to_lsl !== false
    || question.brainbit_to_touchdesigner !== false
  );
  const radarRequested = isStudySensorEnabled('mini_radar') && question.mini_radar_recording_enabled !== false;
  const cameraRequested = isStudySensorEnabled('camera_emotion') && question.camera_capture_enabled === true;
  const anySensorEnabled = hasAnyStudySensorEnabled();
  return (
    anySensorEnabled
    && (
      question.send_signal !== false
      || brainbitRequested
      || radarRequested
      || cameraRequested
    )
  );
}

function getStudySensorSettings() {
  const effective = state.sensorRuntime?.effective;
  if (effective && typeof effective === 'object') {
    return {
      brainbit: effective.brainbit === true,
      mini_radar: effective.mini_radar === true,
      camera_emotion: effective.camera_emotion === true,
    };
  }
  const settings = state.config.study_settings || {};
  if (settings.sensors_enabled === false) {
    return { brainbit: false, mini_radar: false, camera_emotion: false };
  }
  const sensors = settings.sensors && typeof settings.sensors === 'object' ? settings.sensors : {};
  return {
    brainbit: sensors.brainbit !== false,
    mini_radar: sensors.mini_radar !== false,
    camera_emotion: sensors.camera_emotion === true,
  };
}

function isStudySensorEnabled(sensorKey) {
  return Boolean(getStudySensorSettings()[sensorKey]);
}

function hasAnyStudySensorEnabled() {
  return Object.values(getStudySensorSettings()).some(Boolean);
}

async function startStudySensorSession() {
  try {
    const response = await postJson('/api/study/session/start', {
      session_id: state.sessionId,
      client_id: getStudyClientId(),
      study_id: state.config.study_id || '',
      participant_id: resolveParticipantId(),
      current_index: state.currentIndex,
      current_type: (state.config.questions || [])[state.currentIndex]?.type || null,
      require_admin_start: true,
      study_run_id: state.studyRunState?.run_id || '',
    });
    state.sessionId = response.session?.session_id || state.sessionId;
    state.sensorSessionStarted = true;
    return true;
  } catch (error) {
    state.sensorSessionStarted = false;
    console.error('[study] Could not start study sensor session:', error);
    showStudyNotice(t('study.startFailed', 'Could not start the study session.'));
    return false;
  }
}

async function stopStudySensorSession(options = {}) {
  const sessionId = options.sessionId || state.sessionId;
  if (!state.sensorSessionStarted && !sessionId) {
    return;
  }
  const clearSnapshot = options.clearSnapshot !== false;
  try {
    await postJson('/api/study/session/stop', {
      session_id: sessionId,
      client_id: getStudyClientId(),
      study_id: options.studyId || state.config.study_id || '',
      participant_id: options.participantId || resolveParticipantId(),
    }, { timeoutMs: STUDY_SESSION_STOP_TIMEOUT_MS });
  } catch (error) {
    console.error('[study] Could not stop study sensor session:', error);
  } finally {
    state.sensorSessionStarted = false;
    if (clearSnapshot) {
      clearSessionSnapshot();
    }
  }
}

function isAnswered(questionIndex) {
  const question = (state.config.questions || [])[questionIndex];
  if (!question) {
    return true;
  }
  if (question.type === 'stimulus') {
    return true;
  }
  const cardElement = getElement(`card-q-${questionIndex}`);
  if (!cardElement) {
    return true;
  }

  if (question.type === 'slider') {
    return getTouchedFieldCount(questionIndex) >= 1;
  }
  if (question.type === 'multi-slider') {
    return getTouchedFieldCount(questionIndex) >= (question.dimensions?.length || 0);
  }
  if (question.type === 'ranking') {
    return getTouchedFieldCount(questionIndex) >= 1;
  }
  if (question.type === 'text') {
    return (CARDS.text.collectAnswer(questionIndex) || '').trim().length > 0;
  }
  if (question.type === 'mood-meter') {
    const answer = CARDS['mood-meter'].collectAnswer(questionIndex);
    return Array.isArray(answer) && answer.length > 0;
  }
  if (question.type === 'word-cloud') {
    const answer = CARDS['word-cloud'].collectAnswer(questionIndex);
    return Array.isArray(answer) && answer.length > 0;
  }
  if (question.type === 'semantic') {
    return cardElement.querySelectorAll('input[type="radio"]:checked').length >= (question.pairs?.length || 0);
  }
  if (question.type === 'choice') {
    return Boolean(cardElement.querySelector('input[type="checkbox"]:checked'));
  }
  if (question.type === 'single' || question.type === 'likert') {
    return Boolean(cardElement.querySelector('input[type="radio"]:checked'));
  }
  if (question.type === 'participant-id') {
    return CARDS['participant-id'].collectAnswer() !== null;
  }
  if (question.type === 'finish') {
    return true;
  }

  return true;
}

function updateNavigation() {
  const questions = state.config.questions || [];
  const total = questions.length;
  if (!total) {
    getElement('btn-prev').disabled = true;
    getElement('btn-next').disabled = true;
    renderCounter(0, 0);
    updateProgressBar(0, 0);
    getElement('btn-next-label').textContent = t('study.finish', 'Finish');
    getElement('btn-next-icon').className = 'iconoir-check';
    return;
  }

  const currentIndex = state.currentIndex;
  const currentQuestion = questions[currentIndex];
  const totalNormal = questions.filter(q => q.type !== 'finish').length;

  const nav = document.querySelector('.q-nav');
  if (currentQuestion && currentQuestion.type === 'finish') {
    if (nav) nav.style.display = 'none';
    renderCounter(totalNormal, totalNormal);
    updateProgressBar(totalNormal, totalNormal);
    return;
  } else {
    if (nav) nav.style.display = 'flex';
  }

  const isFirst = currentIndex === 0;
  const isStimulusBusy = Boolean(state.activeStimulus);
  const isUiBusy = state.navigationBusy || state.submitInFlight;
  const isOptional = currentQuestion?.required === false;
  const answered = (isOptional || isAnswered(currentIndex)) && !isStimulusBusy;

  const isLastNormalCard = (currentIndex === total - 1) || (questions[currentIndex + 1]?.type === 'finish');
  const isPreStudyStart = !state.startTime && currentQuestion?.type === 'participant-id';

  getElement('btn-prev').disabled = isFirst || isStimulusBusy || isUiBusy;
  getElement('btn-next').disabled = !answered || isUiBusy;

  renderCounter(Math.min(currentIndex + 1, totalNormal), totalNormal);
  updateProgressBar(Math.min(currentIndex + 1, totalNormal), totalNormal);
  getElement('btn-next-label').textContent = state.submitInFlight
    ? t('study.saving', 'Saving...')
    : (state.navigationBusy
      ? (isPreStudyStart ? t('study.starting', 'Starting...') : t('study.pleaseWait', 'Please wait...'))
      : (isPreStudyStart ? t('study.start', 'Start') : (isLastNormalCard ? t('study.submit', 'Submit') : t('study.next', 'Next'))));
  getElement('btn-next-icon').className = isLastNormalCard ? 'iconoir-check' : 'iconoir-nav-arrow-right';
}

function renderCounter(current, total) {
  const pad = (value) => String(value).padStart(2, '0');
  getElement('q-counter').innerHTML = `
    <span class="q-counter-current">${pad(current)}</span>
    <span class="q-counter-divider">/</span>
    <span class="q-counter-total">${pad(total)}</span>`;
}

function updateProgressBar(current, total) {
  const progressBar = getElement('study-progress-bar');
  const progressFill = getElement('study-progress-fill');
  if (!progressBar || !progressFill) {
    return;
  }

  const enabled = state.config.study_settings?.progress_bar_enabled === true;
  if (!enabled || total <= 0) {
    progressBar.hidden = true;
    progressFill.style.width = '0%';
    return;
  }

  const percent = Math.max(0, Math.min(100, (current / total) * 100));
  progressBar.hidden = false;
  progressFill.style.width = `${percent}%`;
}

async function handleNext() {
  if (state.navigationBusy || state.submitInFlight) {
    return;
  }
  const total = (state.config.questions || []).length;
  const nextQuestion = state.config.questions[state.currentIndex + 1];

  const willSubmit = (nextQuestion && nextQuestion.type === 'finish') || state.currentIndex === total - 1;
  if (state.startTime && willSubmit) {
    await submitResults();
    return;
  }

  state.navigationBusy = true;
  updateNavigation();
  if (!state.startTime) {
    try {
      await startTrial({ rebuild: false });
      if (!state.startTime) {
        return;
      }
      if (willSubmit) {
        state.navigationBusy = false;
        updateNavigation();
        await submitResults();
        return;
      }
      await goTo(state.currentIndex + 1, { lockNavigation: false, force: true });
    } finally {
      if (!state.submitInFlight) {
        state.navigationBusy = false;
        updateNavigation();
      }
    }
    return;
  }

  try {
    await goTo(state.currentIndex + 1, { lockNavigation: false, force: true });
  } finally {
    state.navigationBusy = false;
    updateNavigation();
  }
}

function collectAnswers() {
  const answers = {};

  (state.config.questions || []).forEach((question, questionIndex) => {
    if (question.type === 'stimulus' || question.type === 'participant-id' || question.type === 'finish') {
      return;
    }
    // An optional, untouched question is omitted entirely so the server can
    // tell "shown but skipped" apart from "answered" - never send a default.
    if (question.required === false && !isAnswered(questionIndex)) {
      return;
    }

    const cardModule = CARDS[question.type];
    if (cardModule) {
      answers[`q${questionIndex}`] = cardModule.collectAnswer(questionIndex, question);
    }
  });

  return answers;
}

function collectAnswerEvents() {
  const events = [];
  const questions = state.config.questions || [];

  questions.forEach((question, questionIndex) => {
    if (!question || question.type === 'stimulus' || question.type === 'finish') {
      return;
    }

    const metrics = state.questionMetrics[questionIndex] || {};
    if (!metrics.answered_at) {
      return;
    }
    const answerKey = question.type === 'participant-id' ? null : `q${questionIndex}`;
    events.push({
      question_index: questionIndex,
      question_type: question.type,
      answer_key: answerKey,
      shown_at: metrics.shown_at || metrics.answered_at,
      answered_at: metrics.answered_at,
    });
  });

  return events;
}

function collectCardEvents() {
  const events = [];
  const questions = state.config.questions || [];

  questions.forEach((question, questionIndex) => {
    if (!question || question.type === 'finish') {
      return;
    }

    const metrics = state.questionMetrics[questionIndex] || {};
    if (!metrics.shown_at) {
      return;
    }
    const event = {
      question_index: questionIndex,
      question_type: question.type,
      shown_at: metrics.shown_at,
      shown_at_server_epoch_ms: metrics.shown_at_server_epoch_ms || null,
    };

    if (question.type === 'stimulus') {
      event.active_started_at = metrics.active_started_at || null;
      event.active_ended_at = metrics.active_ended_at || metrics.completed_at || null;
      event.completed_at = metrics.completed_at || metrics.active_ended_at || null;
      event.server_start_received_at = metrics.server_start_received_at || null;
      event.server_stop_received_at = metrics.server_stop_received_at || null;
      event.server_start_received_epoch_ms = metrics.server_start_received_epoch_ms || null;
      event.server_stop_received_epoch_ms = metrics.server_stop_received_epoch_ms || null;
      event.client_start_trigger_epoch_ms = metrics.client_start_trigger_epoch_ms || null;
      event.client_stop_trigger_epoch_ms = metrics.client_stop_trigger_epoch_ms || null;
      event.start_marker = metrics.start_marker || '';
      event.stop_marker = metrics.stop_marker || '';
    } else {
      event.answered_at = metrics.answered_at || null;
      event.answered_at_server_epoch_ms = metrics.answered_at_server_epoch_ms || null;
      event.completed_at = metrics.answered_at || null;
    }

    events.push(event);
  });

  return events;
}

async function submitResults() {
  if (state.submitInFlight) {
    return;
  }
  state.submitInFlight = true;
  state.navigationBusy = false;
  const btn = getElement('btn-next');
  if (btn) {
    btn.disabled = true;
    getElement('btn-next-label').textContent = t('study.saving', 'Saving...');
  }
  updateNavigation();

  try {
    void recordQuestionCompletion(state.currentIndex);
    const timestampEnd = new Date().toISOString();
    const currentQuestion = (state.config.questions || [])[state.currentIndex] || null;
    const sessionId = state.sessionId;
    const participantId = resolveParticipantId();
    const studyId = state.config.study_id;
    void sendMarker('study_end', state.currentIndex, currentQuestion, 'study_end');
    stopCameraMonitor();
    const response = await postJson('/api/results', {
      session_id: sessionId,
      participant_id: participantId,
      study_id: studyId,
      client_clock_offset_ms: getClientClockOffsetMs(),
      timestamp_start: new Date(state.startTime).toISOString(),
      timestamp_end: timestampEnd,
      answers: collectAnswers(),
      participant_metadata: collectParticipantMetadata(),
      answer_events: collectAnswerEvents(),
      card_events: collectCardEvents(),
    });
    state.studyRunState = response?.study_run_state || state.studyRunState;
    state.completedLocally = true;
    state.completedRunId = state.studyRunState?.run_id || '';
    clearSessionSnapshot();
    state.startTime = null;
    state.sessionId = '';
    state.sensorSessionStarted = false;
    void stopStudySensorSession({
      sessionId,
      participantId,
      studyId,
      clearSnapshot: false,
    });

    const finishIndex = (state.config.questions || []).findIndex(q => q.type === 'finish');
    if (finishIndex !== -1) {
      await goTo(finishIndex, { force: true, lockNavigation: false });
    } else {
      showScreen('done'); // Fallback when the finish card is missing
    }
    state.submitInFlight = false;
    updateNavigation();
  } catch (error) {
    console.error('[study] Could not save results:', error);
    showStudyNotice(t('study.saveFailedBody', 'Your answers could not be saved. Please tell the study supervisor - your answers are still on this screen.'), 'error', 10000);
    state.submitInFlight = false;
    state.navigationBusy = false;
    if (btn) {
      btn.disabled = false;
      getElement('btn-next-label').textContent = t('study.submit', 'Submit');
    }
    updateNavigation();
  }
}

void init();
