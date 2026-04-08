import { getJson, postJson } from './api-client.js';
import { CARDS } from './cards/index.js';
import { onInput as sliderInput } from './cards/card-slider.js';
import { onClick as rankClick  } from './cards/card-ranking.js';

const state = {
  config:               {},
  startTime:            null,
  currentIndex:         0,
  stimulusTimerActive:  false,
  stimulusTimer:        null,
};

async function init() {
  bindEvents();
  state.config = await getJson('/api/config');
}

function bindEvents() {
  const $ = id => document.getElementById(id);
  $('pid-input').addEventListener('input',  updateStartBtn);
  $('consent-check').addEventListener('change', updateStartBtn);
  $('btn-start').addEventListener('click',  startTrial);
  $('btn-prev').addEventListener('click',   () => goTo(state.currentIndex - 1));
  $('btn-next').addEventListener('click',   handleNext);

  const qc = $('q-container');
  qc.addEventListener('input',  e => sliderInput(e));
  qc.addEventListener('click',  e => rankClick(e));
  // Re-check answered state whenever the participant interacts with a card
  qc.addEventListener('change', () => updateNav());
}

function updateStartBtn() {
  const pid     = document.getElementById('pid-input').value.trim();
  const consent = document.getElementById('consent-check').checked;
  document.getElementById('btn-start').disabled = !(pid && consent);
}

function showScreen(name) {
  document.querySelectorAll('.screen').forEach(s => {
    s.classList.remove('active');
    s.style.animation = 'none';
  });
  const el = document.getElementById(`screen-${name}`);
  el.style.animation = '';
  el.classList.add('active');
}

async function startTrial() {
  state.startTime = Date.now();
  // /api/start is called per stimulus card when it becomes active (send_signal: true)
  buildQuestions();
  showScreen('questions');
}

function buildQuestions() {
  const container = document.getElementById('q-container');
  container.replaceChildren();
  state.currentIndex = 0;
  state.stimulusTimerActive = false;
  if (state.stimulusTimer) { clearInterval(state.stimulusTimer); state.stimulusTimer = null; }

  (state.config.questions || []).forEach((q, i) => {
    const cardModule = CARDS[q.type];
    if (!cardModule) return;

    const wrap = document.createElement('div');
    wrap.className = 'q-card-study' + (i === 0 ? ' active' : '');
    wrap.id = `card-q-${i}`;
    wrap.innerHTML = cardModule.renderStudy(q, i);
    container.appendChild(wrap);
  });

  updateNav();

  // If the first card is a stimulus, start its countdown immediately
  const firstQuestion = (state.config.questions || [])[0];
  if (firstQuestion?.type === 'stimulus') {
    startStimulusCard(0, firstQuestion);
  }
}

// Navigate to a specific question index with a directional slide animation
function goTo(targetIndex) {
  const total = (state.config.questions || []).length;
  if (targetIndex < 0 || targetIndex >= total) return;

  // Cancel any running stimulus timer before navigating away
  if (state.stimulusTimer) {
    clearInterval(state.stimulusTimer);
    state.stimulusTimer = null;
    state.stimulusTimerActive = false;
  }

  const fromEl = document.getElementById(`card-q-${state.currentIndex}`);
  const toEl   = document.getElementById(`card-q-${targetIndex}`);
  if (!fromEl || !toEl) return;

  const goingForward = targetIndex > state.currentIndex;

  fromEl.classList.remove('active');
  fromEl.classList.add(goingForward ? 'exit-left' : 'exit-right');

  toEl.classList.add('active', goingForward ? 'enter-right' : 'enter-left');

  const cleanup = () => {
    fromEl.classList.remove('exit-left', 'exit-right');
    toEl.classList.remove('enter-right', 'enter-left');
  };
  toEl.addEventListener('animationend', cleanup, { once: true });

  state.currentIndex = targetIndex;
  updateNav();

  // If the target card is a stimulus, start its countdown
  const targetQuestion = (state.config.questions || [])[targetIndex];
  if (targetQuestion?.type === 'stimulus') {
    startStimulusCard(targetIndex, targetQuestion);
  }
}

async function startStimulusCard(index, q) {
  state.stimulusTimerActive = true;
  updateNav(); // disables prev/next during countdown

  if (q.send_signal !== false) {
    await postJson('/api/start', {});
  }
  applyStimulusContent(index, q);

  const durationSeconds = Math.max(1, Math.round((q.duration_ms || 30000) / 1000));
  const ringElement     = document.getElementById(`ring-prog-${index}`);
  const numElement      = document.getElementById(`cd-num-${index}`);
  let elapsed = 0;
  if (numElement) numElement.textContent = durationSeconds;

  state.stimulusTimer = setInterval(async () => {
    elapsed += 1;
    if (numElement) numElement.textContent = Math.max(0, durationSeconds - elapsed);
    if (ringElement) ringElement.style.strokeDashoffset = 314 * (elapsed / durationSeconds);

    if (elapsed >= durationSeconds) {
      clearInterval(state.stimulusTimer);
      state.stimulusTimer = null;
      state.stimulusTimerActive = false;
      if (q.send_signal !== false) await postJson('/api/stop', {});
      handleNext(); // auto-advance to next card
    }
  }, 1000);
}

function applyStimulusContent(index, q) {
  const contentElement = document.getElementById(`stimulus-content-${index}`);
  if (!contentElement) return;
  contentElement.innerHTML = '';
  contentElement.hidden = true;

  const triggerType    = q.trigger_type    || 'timer';
  const triggerContent = q.trigger_content || '';

  if (triggerType === 'image' && triggerContent) {
    const image = document.createElement('img');
    image.src = triggerContent; image.className = 'stimulus-image'; image.alt = '';
    contentElement.appendChild(image); contentElement.hidden = false;

  } else if (triggerType === 'video' && triggerContent) {
    const video = document.createElement('video');
    video.src = triggerContent; video.className = 'stimulus-video';
    video.autoplay = true; video.loop = true; video.muted = true; video.playsInline = true;
    contentElement.appendChild(video); contentElement.hidden = false;

  } else if (triggerType === 'audio' && triggerContent) {
    const audio = document.createElement('audio');
    audio.src = triggerContent; audio.autoplay = true; audio.loop = true;
    contentElement.appendChild(audio); // audio is not visual; container stays hidden

  } else if (triggerType === 'html' && triggerContent) {
    // Admin-authored HTML content (not participant input) — treated as trusted researcher content
    contentElement.innerHTML = triggerContent; contentElement.hidden = false;

  } else if (triggerType === 'js' && triggerContent) {
    // Admin-authored JS snippet — trusted researcher content, not participant input.
    // The study helper is injected so snippets can call Flask API endpoints cleanly
    // without needing global fetch. Example: study.call('/api/brainbit/start', {})
    const studyHelper = {
      call: (path, data = {}) => fetch(path, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(data),
      }).then(response => response.json()),
    };
    try {
      (new Function('study', triggerContent))(studyHelper);
    } catch (error) {
      console.error('[stimulus] Custom JavaScript error:', error);
    }
  }
}

// Returns true when the current question has a response recorded in the DOM.
// Stimulus, slider, ranking, and text always count as answered (auto or have default values).
function isAnswered(index) {
  const q = (state.config.questions || [])[index];
  if (!q) return true;
  if (q.type === 'stimulus') return true;
  if (['slider', 'ranking', 'text'].includes(q.type)) return true;

  const cardEl = document.getElementById(`card-q-${index}`);
  if (!cardEl) return true;
  return !!cardEl.querySelector('input[type=radio]:checked, input[type=checkbox]:checked');
}

function updateNav() {
  const total     = (state.config.questions || []).length;
  const i         = state.currentIndex;
  const isFirst   = i === 0;
  const isLast    = i === total - 1;
  const answered  = isAnswered(i) && !state.stimulusTimerActive;

  document.getElementById('btn-prev').disabled = isFirst || state.stimulusTimerActive;
  // Next is enabled only when the current question has been answered
  document.getElementById('btn-next').disabled = !answered;

  // Counter in the style of the Fluid Intelligence data-label (zero-padded mono)
  const pad = n => String(n).padStart(2, '0');
  document.getElementById('q-counter').textContent = `${pad(i + 1)} / ${pad(total)}`;

  // On the last question the next button becomes the submit action
  document.getElementById('btn-next-label').textContent = isLast ? 'Submit' : 'Next';
  document.getElementById('btn-next-icon').className    = isLast ? 'iconoir-check' : 'iconoir-nav-arrow-right';
}

function handleNext() {
  const total = (state.config.questions || []).length;
  if (state.currentIndex === total - 1) {
    submitResults();
  } else {
    goTo(state.currentIndex + 1);
  }
}

function collectAnswers() {
  const answers = {};
  // All card DOM elements are kept in the container throughout the session,
  // so answer collection works even for cards the participant navigated away from
  (state.config.questions || []).forEach((q, i) => {
    if (q.type === 'stimulus') return; // stimulus cards have no participant answer
    const cardModule = CARDS[q.type];
    if (cardModule) answers[`q${i}`] = cardModule.collectAnswer(i, q);
  });
  return answers;
}

async function submitResults() {
  await postJson('/api/results', {
    participant_id:  document.getElementById('pid-input').value.trim(),
    study_id:        state.config.study_id,
    timestamp_start: new Date(state.startTime).toISOString(),
    timestamp_end:   new Date().toISOString(),
    answers:         collectAnswers(),
  });
  showScreen('done');
}

init();
