function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');
}

export const meta = {
  type: 'stimulus',
  icon: 'timer',
  label: 'Stimulus / Countdown',
};

export const defaultQuestion = {
  type: 'stimulus',
  title: 'Observe the material',
  subtitle: 'Pay attention to all sensory impressions. The questionnaire will appear automatically.',
  warmup_duration_ms: 0,
  duration_ms: 30000,
  trigger_type: 'timer',
  trigger_content: '',
  send_signal: true,
  brainbit_to_lsl: true,
  brainbit_to_touchdesigner: true,
  camera_capture_enabled: false,
  camera_snapshot_interval_ms: 1000,
  mini_radar_recording_enabled: true,
};

export function renderStudy(q, i) {
  const warmupSeconds = Math.max(0, Math.round((q.warmup_duration_ms || 0) / 1000));
  const durationSeconds = Math.max(1, Math.round((q.duration_ms || 30000) / 1000));
  const startsWithWarmup = warmupSeconds > 0;

  return `
    <div
      class="stimulus-body ${startsWithWarmup ? 'stimulus-body--warmup' : 'stimulus-body--active'}"
      id="stimulus-shell-${i}"
      data-phase="${startsWithWarmup ? 'warmup' : 'active'}"
    >
      <div class="stimulus-stage stimulus-stage--warmup" id="stimulus-warmup-${i}"${startsWithWarmup ? '' : ' hidden'}>
        <div class="q-type-tag"><i class="iconoir-spark"></i> Prepare</div>
        <div class="stimulus-copy-wrap">
          <h1 class="stimulus-hero-title">${escapeHtml(q.title || 'Observe the material')}</h1>
          <p class="stimulus-hero-sub">${escapeHtml(q.subtitle || '')}</p>
        </div>
        <div class="stimulus-mini-timer" id="stimulus-mini-timer-${i}">
          <span class="stimulus-mini-label">Starts in</span>
          <span class="stimulus-mini-value" id="warmup-num-${i}">${warmupSeconds}</span>
        </div>
      </div>

      <div class="stimulus-stage stimulus-stage--active" id="stimulus-active-${i}"${startsWithWarmup ? ' hidden' : ''}>
        <div class="q-type-tag"><i class="iconoir-timer"></i> Stimulus active</div>
        <div class="stimulus-active-copy">
          <h1 class="screen-title">${escapeHtml(q.title || 'Observe the material')}</h1>
          <p class="screen-sub">${escapeHtml(q.subtitle || '')}</p>
        </div>
        <div class="stimulus-content" id="stimulus-content-${i}" hidden></div>
        <svg class="cd-ring" viewBox="0 0 120 120" aria-hidden="true">
          <circle cx="60" cy="60" r="50" fill="none" stroke="var(--ink-06)" stroke-width="5"></circle>
          <circle
            class="cd-ring-progress"
            id="ring-prog-${i}"
            cx="60"
            cy="60"
            r="50"
            fill="none"
            stroke="var(--accent)"
            stroke-width="5"
            stroke-linecap="round"
            stroke-dasharray="314"
            stroke-dashoffset="0"
            transform="rotate(-90 60 60)"
          ></circle>
        </svg>
        <div class="cd-num" id="cd-num-${i}">${durationSeconds}</div>
        <div class="cd-lbl">seconds remaining</div>
      </div>
    </div>`;
}

export function renderEditor(q) {
  const warmupSeconds = Math.max(0, Math.round((q.warmup_duration_ms || 0) / 1000));
  const durationSeconds = Math.max(1, Math.round((q.duration_ms || 30000) / 1000));
  const triggerType = q.trigger_type || 'timer';
  const triggerTypes = ['timer', 'image', 'video', 'audio', 'html', 'js'];
  const isContentHidden = triggerType === 'timer';
  const isCode = triggerType === 'html' || triggerType === 'js';

  return `
    <div class="field">
      <label>Title</label>
      <input type="text" class="se-title" value="${escapeHtml(q.title || '')}">
    </div>
    <div class="field">
      <label>Subtitle / instruction</label>
      <textarea class="se-subtitle" rows="3">${escapeHtml(q.subtitle || '')}</textarea>
    </div>
    <div class="row2">
      <div class="field">
        <label>Warm-up (seconds before start)</label>
        <input type="number" class="se-warmup-duration" min="0" max="600" value="${warmupSeconds}">
      </div>
      <div class="field">
        <label>Active duration (seconds)</label>
        <input type="number" class="se-duration" min="1" max="600" value="${durationSeconds}">
      </div>
    </div>
    <div class="field">
      <label>Trigger type</label>
      <div class="trigger-type-pills">
        ${triggerTypes.map(type => `
          <button type="button" class="trigger-pill${triggerType === type ? ' active' : ''}" data-trigger-type="${escapeHtml(type)}">
            ${escapeHtml(type)}
          </button>`).join('')}
      </div>
      <input type="hidden" class="se-trigger-type" value="${escapeHtml(triggerType)}">
    </div>
    <div class="field se-trigger-content-field"${isContentHidden ? ' hidden' : ''}>
      <label>${isCode ? 'Code' : 'URL'}</label>
      ${isCode
        ? `<textarea class="se-trigger-content se-trigger-content--code" rows="6" placeholder="Paste ${escapeHtml(triggerType)} code here...">${escapeHtml(q.trigger_content || '')}</textarea>`
        : `<input type="url" class="se-trigger-content" placeholder="https://..." value="${escapeHtml(q.trigger_content || '')}">`
      }
    </div>
    <div class="field">
      <label class="checkbox-row">
        <input type="checkbox" class="se-send-signal"${q.send_signal !== false ? ' checked' : ''}>
        <span>Send Study Runner start/stop signals when the active phase begins and ends</span>
      </label>
    </div>
    <div class="field">
      <label class="checkbox-row">
        <input type="checkbox" class="se-brainbit-lsl"${q.brainbit_to_lsl !== false ? ' checked' : ''}>
        <span>Forward BrainBit data to LSL during this active phase</span>
      </label>
    </div>
    <div class="field">
      <label class="checkbox-row">
        <input type="checkbox" class="se-brainbit-touchdesigner"${q.brainbit_to_touchdesigner !== false ? ' checked' : ''}>
        <span>Forward BrainBit data to TouchDesigner during this active phase</span>
      </label>
    </div>
    <div class="field">
      <label class="checkbox-row">
        <input type="checkbox" class="se-mini-radar-recording"${q.mini_radar_recording_enabled !== false ? ' checked' : ''}>
        <span>Record Mini-radar pulse and breathing during this active phase</span>
      </label>
    </div>
    <div class="field">
      <label class="checkbox-row">
        <input type="checkbox" class="se-camera-capture"${q.camera_capture_enabled === true ? ' checked' : ''}>
        <span>Capture iPad camera snapshots for camera emotion analysis during this active phase</span>
      </label>
    </div>
    <div class="field">
      <label>Camera snapshot interval (ms)</label>
      <input type="number" class="se-camera-interval" min="250" max="60000" step="250" value="${Number(q.camera_snapshot_interval_ms || 1000)}">
    </div>
    <p class="stimulus-editor-note">
      Warm-up only shows the instruction view. Study signals, BrainBit routing, Mini-radar recording, camera snapshots, media triggers, and custom JS start when the active timer begins.
      HTML and JS trigger types stay blocked unless the server explicitly enables <code>STUDY_RUNNER_ALLOW_UNSAFE_STIMULUS_CODE=1</code>.
    </p>`;
}

export function collectConfig(el) {
  return {
    type: 'stimulus',
    title: el.querySelector('.se-title')?.value.trim() || '',
    subtitle: el.querySelector('.se-subtitle')?.value.trim() || '',
    warmup_duration_ms: Number.parseInt(el.querySelector('.se-warmup-duration')?.value || '0', 10) * 1000,
    duration_ms: Number.parseInt(el.querySelector('.se-duration')?.value || '30', 10) * 1000,
    trigger_type: el.querySelector('.se-trigger-type')?.value || 'timer',
    trigger_content: el.querySelector('.se-trigger-content')?.value.trim() || '',
    send_signal: el.querySelector('.se-send-signal')?.checked ?? true,
    brainbit_to_lsl: el.querySelector('.se-brainbit-lsl')?.checked ?? true,
    brainbit_to_touchdesigner: el.querySelector('.se-brainbit-touchdesigner')?.checked ?? true,
    mini_radar_recording_enabled: el.querySelector('.se-mini-radar-recording')?.checked ?? true,
    camera_capture_enabled: el.querySelector('.se-camera-capture')?.checked ?? false,
    camera_snapshot_interval_ms: Number.parseInt(el.querySelector('.se-camera-interval')?.value || '1000', 10),
  };
}

export function collectAnswer() {
  return null;
}
