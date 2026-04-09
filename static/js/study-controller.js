import { getJson, postJson } from './api-client.js';
import { CARDS } from './cards/index.js';
import { onInput as sliderInput } from './cards/card-slider.js';
import { onClick as rankClick } from './cards/card-ranking.js';

const state = {
  config: {},
  startTime: null,
  currentIndex: 0,
  activeStimulus: null,
};

function getElement(id) {
  return document.getElementById(id);
}

async function init() {
  bindEvents();

  try {
    state.config = await getJson('/api/config');
  } catch (error) {
    console.error('[study] Could not load configuration:', error);
    alert(`Could not load the study configuration: ${error.message}`);
  }
}

function bindEvents() {
  getElement('pid-input').addEventListener('input', updateStartButton);
  getElement('consent-check').addEventListener('change', updateStartButton);
  getElement('btn-start').addEventListener('click', () => void startTrial());
  getElement('btn-prev').addEventListener('click', () => void goTo(state.currentIndex - 1));
  getElement('btn-next').addEventListener('click', () => void handleNext());

  const questionContainer = getElement('q-container');
  questionContainer.addEventListener('input', (event) => sliderInput(event));
  questionContainer.addEventListener('click', (event) => rankClick(event));
  questionContainer.addEventListener('change', updateNavigation);
}

function updateStartButton() {
  const participantId = getElement('pid-input').value.trim();
  const hasConsent = getElement('consent-check').checked;
  getElement('btn-start').disabled = !(participantId && hasConsent);
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
      await postJson('/api/start', {
        send_signal: question.send_signal !== false,
        brainbit_to_lsl: question.brainbit_to_lsl !== false,
        brainbit_to_touchdesigner: question.brainbit_to_touchdesigner !== false,
      });
      stimulusRun.signalStarted = true;
    } catch (error) {
      console.error('[study] Could not send /api/start:', error);
    }
  }

  stimulusRun.cleanup = applyStimulusContent(index, question);
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
      await postJson('/api/stop', {
        send_signal: stimulusRun.question.send_signal !== false,
        brainbit_to_lsl: stimulusRun.question.brainbit_to_lsl !== false,
        brainbit_to_touchdesigner: stimulusRun.question.brainbit_to_touchdesigner !== false,
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
    contentElement.innerHTML = triggerContent;
    contentElement.hidden = false;
  } else if (triggerType === 'js' && triggerContent) {
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

function shouldActivateHardware(question) {
  return (
    question.send_signal !== false
    || question.brainbit_to_lsl !== false
    || question.brainbit_to_touchdesigner !== false
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
  if (['slider', 'ranking', 'text'].includes(question.type)) {
    return true;
  }

  const cardElement = getElement(`card-q-${questionIndex}`);
  if (!cardElement) {
    return true;
  }

  return Boolean(cardElement.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked'));
}

function updateNavigation() {
  const total = (state.config.questions || []).length;
  if (!total) {
    getElement('btn-prev').disabled = true;
    getElement('btn-next').disabled = true;
    getElement('q-counter').textContent = '00 / 00';
    getElement('btn-next-label').textContent = 'Finish';
    getElement('btn-next-icon').className = 'iconoir-check';
    return;
  }

  const currentIndex = state.currentIndex;
  const isFirst = currentIndex === 0;
  const isLast = currentIndex === total - 1;
  const isStimulusBusy = Boolean(state.activeStimulus);
  const answered = isAnswered(currentIndex) && !isStimulusBusy;

  getElement('btn-prev').disabled = isFirst || isStimulusBusy;
  getElement('btn-next').disabled = !answered;

  const pad = (value) => String(value).padStart(2, '0');
  getElement('q-counter').textContent = `${pad(currentIndex + 1)} / ${pad(total)}`;
  getElement('btn-next-label').textContent = isLast ? 'Submit' : 'Next';
  getElement('btn-next-icon').className = isLast ? 'iconoir-check' : 'iconoir-nav-arrow-right';
}

async function handleNext() {
  const total = (state.config.questions || []).length;
  if (state.currentIndex === total - 1) {
    await submitResults();
    return;
  }

  await goTo(state.currentIndex + 1);
}

function collectAnswers() {
  const answers = {};

  (state.config.questions || []).forEach((question, questionIndex) => {
    if (question.type === 'stimulus') {
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
  try {
    await postJson('/api/results', {
      participant_id: getElement('pid-input').value.trim(),
      study_id: state.config.study_id,
      timestamp_start: new Date(state.startTime).toISOString(),
      timestamp_end: new Date().toISOString(),
      answers: collectAnswers(),
    });

    showScreen('done');
  } catch (error) {
    console.error('[study] Could not save results:', error);
    alert(`Could not save the results: ${error.message}`);
  }
}

void init();



