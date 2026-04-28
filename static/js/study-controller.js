import { getJson, postJson } from './api-client.js';
import { startCameraCaptureSession } from './camera-capture.js';
import { CARDS } from './cards/index.js';
import { onInput as sliderInput } from './cards/card-slider.js';
import { bindDrag as rankBindDrag } from './cards/card-ranking.js';
import { onClick as moodMeterClick } from './cards/card-mood-meter.js';
import { bindCardEvents as bindWordCloudEvents } from './cards/card-word-cloud.js';
import { startStudyClientHeartbeat } from './study-client-heartbeat.js';

const state = {
  config: {},
  startTime: null,
  currentIndex: 0,
  activeStimulus: null,
  cameraPermission: 'not_requested',
  clockOffsetMs: null,  // estimated offset between iPad performance.now() and Pi server clock
  touchedFields: {},
};

function getElement(id) {
  return document.getElementById(id);
}

async function init() {
  bindEvents();
  startStudyClientHeartbeat(getStudyClientHeartbeatPayload);

  try {
    state.config = await getJson('/api/config');
    // Starte die Studie sofort, da der Welcome-Screen entfernt wurde
    void startTrial();
  } catch (error) {
    console.error('[study] Could not load configuration:', error);
    alert(`Could not load the study configuration: ${error.message}`);
  }

  // Estimate clock offset between iPad and Pi server for precise trigger timestamps.
  // Runs in background — does not block study start.
  void syncClock();
}

/**
 * Estimate clock offset between iPad performance.now() and Pi server clock.
 * Runs 3 ping-pong rounds and uses the median offset.
 * Algorithm: offset = ((srv_recv - cli_send) + (srv_send - cli_recv)) / 2
 */
async function syncClock() {
  const ROUNDS = 3;
  const offsets = [];

  for (let i = 0; i < ROUNDS; i++) {
    const clientSendMs = performance.now();
    try {
      const resp = await postJson('/api/sync-clock', { client_send_ms: clientSendMs });
      const clientRecvMs = performance.now();
      const srvRecv = resp.server_receive_ms;
      const srvSend = resp.server_send_ms;
      // Convert server timestamps (epoch ms) to performance.now() domain via Date.now()
      const nowEpoch = Date.now();
      const nowPerf = performance.now();
      const srvRecvPerf = srvRecv - nowEpoch + nowPerf;
      const srvSendPerf = srvSend - nowEpoch + nowPerf;
      const offset = ((srvRecvPerf - clientSendMs) + (srvSendPerf - clientRecvMs)) / 2;
      offsets.push(offset);
    } catch {
      // Server unreachable — skip round
    }
    // Small delay between rounds to avoid burst
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  if (offsets.length > 0) {
    offsets.sort((a, b) => a - b);
    state.clockOffsetMs = offsets[Math.floor(offsets.length / 2)];
    console.debug('[study] Clock offset estimated:', state.clockOffsetMs.toFixed(2), 'ms');
  }
}

function bindEvents() {
  getElement('btn-prev').addEventListener('click', () => void goTo(state.currentIndex - 1));
  getElement('btn-next').addEventListener('click', () => void handleNext());

  const questionContainer = getElement('q-container');
  questionContainer.addEventListener('input', handleQuestionInput);
  questionContainer.addEventListener('click', (event) => moodMeterClick(event));
  questionContainer.addEventListener('change', handleQuestionChange);
  questionContainer.addEventListener('ranking:changed', handleQuestionChange);
  questionContainer.addEventListener('wordcloud:changed', handleQuestionChange);
  questionContainer.addEventListener('moodmeter:changed', handleQuestionChange);
  questionContainer.addEventListener('participantid:changed', handleQuestionChange);
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
}

function resolveParticipantId() {
  const questions = state.config.questions || [];
  const pidIdx = questions.findIndex(q => q.type === 'participant-id');
  if (pidIdx >= 0) {
    return CARDS['participant-id'].collectAnswer() || '';
  }
  return 'unknown';
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

async function startTrial() {
  if (!Array.isArray(state.config.questions)) {
    alert('The study configuration is not ready yet. Please reload the page.');
    return;
  }

  state.startTime = Date.now();
  buildQuestions();

  if (!state.config.questions.length) {
    await submitResults();
    return;
  }

  showScreen('questions');
}

function buildQuestions() {
  void stopActiveStimulus({ shouldSendStop: false });

  const container = getElement('q-container');
  container.replaceChildren();
  state.currentIndex = 0;
  state.touchedFields = {};

  (state.config.questions || []).forEach((question, questionIndex) => {
    const cardModule = CARDS[question.type];
    if (!cardModule) {
      return;
    }

    const cardElement = document.createElement('div');
    cardElement.className = 'q-card-study';
    cardElement.id = `card-q-${questionIndex}`;
    cardElement.innerHTML = cardModule.renderStudy(question, questionIndex);
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
  }

  updateNavigation();

  const firstQuestion = (state.config.questions || [])[0];
  if (firstQuestion?.type === 'stimulus') {
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
async function goTo(targetIndex) {
  const total = (state.config.questions || []).length;
  if (targetIndex < 0 || targetIndex >= total) {
    return;
  }

  const shouldSendStop = Boolean(state.activeStimulus?.signalStarted);
  await stopActiveStimulus({ shouldSendStop });

  const currentCard = getElement(`card-q-${state.currentIndex}`);
  const targetCard = getElement(`card-q-${targetIndex}`);
  if (!currentCard || !targetCard) {
    return;
  }

  const goingForward = targetIndex > state.currentIndex;

  currentCard.classList.remove('active');
  clearCardAnimationClasses(currentCard);
  playCardEntrance(targetCard, goingForward ? 'enter-right' : 'enter-left');

  state.currentIndex = targetIndex;
  updateNavigation();

  const targetQuestion = (state.config.questions || [])[targetIndex];
  if (targetQuestion?.type === 'stimulus') {
    void startStimulusCard(targetIndex, targetQuestion);
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
      await postJson('/api/start', {
        send_signal: question.send_signal !== false,
        brainbit_to_lsl: question.brainbit_to_lsl !== false,
        brainbit_to_touchdesigner: question.brainbit_to_touchdesigner !== false,
        mini_radar_recording_enabled: question.mini_radar_recording_enabled !== false,
        client_trigger_ms: clientTriggerMs,
        clock_offset_ms: state.clockOffsetMs,
      });
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
  if (question.camera_capture_enabled !== true) {
    return null;
  }

  return startCameraCaptureSession({
    intervalMs: question.camera_snapshot_interval_ms || 1000,
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
      await postJson('/api/stop', {
        send_signal: stimulusRun.question.send_signal !== false,
        brainbit_to_lsl: stimulusRun.question.brainbit_to_lsl !== false,
        brainbit_to_touchdesigner: stimulusRun.question.brainbit_to_touchdesigner !== false,
        mini_radar_recording_enabled: false,
        client_trigger_ms: clientTriggerMs,
        clock_offset_ms: state.clockOffsetMs,
      });
    } catch (error) {
      console.error('[study] Could not send /api/stop:', error);
    }
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
    current_index: Number.isInteger(state.currentIndex) ? state.currentIndex : null,
    current_type: currentQuestion?.type || null,
    is_stimulus_active: Boolean(state.activeStimulus),
    signal_started: Boolean(state.activeStimulus?.signalStarted),
    camera_permission: state.cameraPermission,
    study_started: Boolean(state.startTime),
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

function getTouchedFieldCount(questionIndex) {
  return state.touchedFields[questionIndex]?.size || 0;
}

function shouldActivateHardware(question) {
  if (state.config.study_settings && state.config.study_settings.sensors_enabled === false) {
    return false;
  }
  return (
    question.send_signal !== false
    || question.brainbit_to_lsl !== false
    || question.brainbit_to_touchdesigner !== false
    || question.mini_radar_recording_enabled !== false
  );
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
    getElement('q-counter').textContent = '00 / 00';
    getElement('btn-next-label').textContent = 'Finish';
    getElement('btn-next-icon').className = 'iconoir-check';
    return;
  }

  const currentIndex = state.currentIndex;
  const currentQuestion = questions[currentIndex];

  const nav = document.querySelector('.q-nav');
  if (currentQuestion && currentQuestion.type === 'finish') {
    if (nav) nav.style.display = 'none';
    return;
  } else {
    if (nav) nav.style.display = 'flex';
  }

  const isFirst = currentIndex === 0;
  const isStimulusBusy = Boolean(state.activeStimulus);
  const answered = isAnswered(currentIndex) && !isStimulusBusy;

  const isLastNormalCard = (currentIndex === total - 1) || (questions[currentIndex + 1]?.type === 'finish');

  getElement('btn-prev').disabled = isFirst || isStimulusBusy;
  getElement('btn-next').disabled = !answered;

  const totalNormal = questions.filter(q => q.type !== 'finish').length;
  const pad = (value) => String(value).padStart(2, '0');
  getElement('q-counter').textContent = `${pad(Math.min(currentIndex + 1, totalNormal))} / ${pad(totalNormal)}`;
  getElement('btn-next-label').textContent = isLastNormalCard ? 'Submit' : 'Next';
  getElement('btn-next-icon').className = isLastNormalCard ? 'iconoir-check' : 'iconoir-nav-arrow-right';
}

async function handleNext() {
  const total = (state.config.questions || []).length;
  const nextQuestion = state.config.questions[state.currentIndex + 1];

  if ((nextQuestion && nextQuestion.type === 'finish') || state.currentIndex === total - 1) {
    await submitResults();
    return;
  }

  await goTo(state.currentIndex + 1);
}

function collectAnswers() {
  const answers = {};

  (state.config.questions || []).forEach((question, questionIndex) => {
    if (question.type === 'stimulus' || question.type === 'participant-id' || question.type === 'finish') {
      return;
    }

    const cardModule = CARDS[question.type];
    if (cardModule) {
      answers[`q${questionIndex}`] = cardModule.collectAnswer(questionIndex, question);
    }
  });

  return answers;
}

async function submitResults() {
  const btn = getElement('btn-next');
  if (btn) {
    btn.disabled = true;
    getElement('btn-next-label').textContent = 'Saving...';
  }

  try {
    await postJson('/api/results', {
      participant_id: resolveParticipantId(),
      study_id: state.config.study_id,
      timestamp_start: new Date(state.startTime).toISOString(),
      timestamp_end: new Date().toISOString(),
      answers: collectAnswers(),
    });

    const finishIndex = (state.config.questions || []).findIndex(q => q.type === 'finish');
    if (finishIndex !== -1) {
      await goTo(finishIndex);
    } else {
      showScreen('done'); // Fallback, falls die Karte fehlt
    }
  } catch (error) {
    console.error('[study] Could not save results:', error);
    alert(`Could not save the results: ${error.message}`);
    if (btn) {
      btn.disabled = false;
      getElement('btn-next-label').textContent = 'Submit';
    }
  }
}

void init();
