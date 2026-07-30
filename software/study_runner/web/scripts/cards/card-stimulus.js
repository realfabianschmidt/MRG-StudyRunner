import { t } from '../i18n.js';
import { escapeHtml } from '../lib/dom-utils.js';

const CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS = 1000;
const CAMERA_SNAPSHOT_MIN_INTERVAL_MS = 1000;
const CAMERA_SNAPSHOT_MAX_INTERVAL_MS = 60000;

export const meta = {
  type: 'stimulus',
  icon: 'timer',
  label: 'Stimulus / Countdown',
  suppressSharedInfoTop: true,
};

export const defaultQuestion = {
  type: 'stimulus',
  title: 'Observe the material',
  info_top: 'Pay attention to all sensory impressions. The questionnaire will appear automatically.',
  warmup_duration_ms: 0,
  duration_ms: 30000,
  trigger_type: 'timer',
  trigger_content: '',
  send_signal: true,
  brainbit_to_lsl: true,
  brainbit_to_touchdesigner: true,
  camera_capture_enabled: false,
  camera_snapshot_interval_ms: CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS,
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
        <div class="q-type-tag"><i class="iconoir-spark"></i> ${escapeHtml(t('stimulus.prepare', 'Prepare'))}</div>
        <div class="stimulus-copy-wrap">
          <h1 class="stimulus-hero-title">${escapeHtml(q.title || 'Observe the material')}</h1>
          <p class="stimulus-hero-sub">${escapeHtml(q.info_top || q.subtitle || '')}</p>
        </div>
        <div class="stimulus-mini-timer" id="stimulus-mini-timer-${i}">
          <span class="stimulus-mini-label">${escapeHtml(t('stimulus.startsIn', 'Starts in'))}</span>
          <span class="stimulus-mini-value" id="warmup-num-${i}">${warmupSeconds}</span>
        </div>
      </div>

      <div class="stimulus-stage stimulus-stage--active" id="stimulus-active-${i}"${startsWithWarmup ? ' hidden' : ''}>
        <div class="q-type-tag"><i class="iconoir-timer"></i> ${escapeHtml(t('stimulus.active', 'Stimulus active'))}</div>
        <div class="stimulus-active-copy">
          <h1 class="screen-title">${escapeHtml(q.title || 'Observe the material')}</h1>
          <p class="screen-sub">${escapeHtml(q.info_top || q.subtitle || '')}</p>
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
        <div class="cd-lbl">${escapeHtml(t('stimulus.secondsRemaining', 'seconds remaining'))}</div>
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
  const cameraInterval = Math.max(
    CAMERA_SNAPSHOT_MIN_INTERVAL_MS,
    Number(q.camera_snapshot_interval_ms || CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS),
  );

  return `
    <div class="field">
      <label>${escapeHtml(t('stimulus.titleLabel', 'Title'))}</label>
      <input type="text" class="se-title" value="${escapeHtml(q.title || '')}">
    </div>
    <div class="row2">
      <div class="field">
        <label>${escapeHtml(t('stimulus.warmupLabel', 'Warm-up (seconds before start)'))}</label>
        <input type="number" class="se-warmup-duration" min="0" max="600" value="${warmupSeconds}">
      </div>
      <div class="field">
        <label>${escapeHtml(t('stimulus.durationLabel', 'Active duration (seconds)'))}</label>
        <input type="number" class="se-duration" min="1" max="600" value="${durationSeconds}">
      </div>
    </div>
    <div class="field">
      <label>${escapeHtml(t('stimulus.triggerTypeLabel', 'Trigger type'))}</label>
      <div class="trigger-type-pills">
        ${triggerTypes.map(type => `
          <button type="button" class="trigger-pill${triggerType === type ? ' active' : ''}" data-trigger-type="${escapeHtml(type)}">
            ${escapeHtml(type)}
          </button>`).join('')}
      </div>
      <input type="hidden" class="se-trigger-type" value="${escapeHtml(triggerType)}">
    </div>
    <div class="field se-trigger-content-field"${isContentHidden ? ' hidden' : ''}>
      <label>${isCode ? escapeHtml(t('stimulus.codeLabel', 'Code')) : escapeHtml(t('stimulus.urlLabel', 'URL'))}</label>
      ${isCode
        ? `<textarea class="se-trigger-content se-trigger-content--code" rows="6" placeholder="${escapeHtml(t('stimulus.codePlaceholder', 'Paste {type} code here...').replace('{type}', triggerType))}">${escapeHtml(q.trigger_content || '')}</textarea>`
        : `<input type="url" class="se-trigger-content" placeholder="${escapeHtml(t('stimulus.urlPlaceholder', 'https://...'))}" value="${escapeHtml(q.trigger_content || '')}">`
      }
    </div>
    <div class="field">
      <label>${escapeHtml(t('stimulus.signalSettingsLabel', 'Signals and recordings'))}</label>
      <div class="stimulus-toggle-list">
        ${renderToggleRow({
          inputClass: 'se-send-signal',
          checked: q.send_signal !== false,
          label: t('stimulus.sendSignalLabel', 'Send Study Runner start/stop signals when the active phase begins and ends'),
        })}
        ${renderToggleRow({
          inputClass: 'se-brainbit-touchdesigner',
          checked: q.brainbit_to_touchdesigner !== false,
          label: t('stimulus.touchdesignerLabel', 'Forward BrainBit data to TouchDesigner during this active phase'),
        })}
        ${renderToggleRow({
          inputClass: 'se-mini-radar-recording',
          checked: q.mini_radar_recording_enabled !== false,
          label: t('stimulus.radarRecordingLabel', 'Record Mini-radar pulse and breathing during this active phase'),
        })}
        ${renderToggleRow({
          inputClass: 'se-camera-capture',
          checked: q.camera_capture_enabled === true,
          label: t('stimulus.cameraCaptureLabel', 'Capture tablet selfie-camera snapshots for camera emotion analysis during this active phase'),
          settings: 'camera',
        })}
      </div>
      <input type="hidden" class="se-camera-interval" value="${cameraInterval}">
    </div>
    <p class="stimulus-editor-note">
      ${escapeHtml(t('stimulus.editorNote', 'Warm-up only shows the instruction view. Study signals, BrainBit routing, Mini-radar recording, camera snapshots, media triggers, and custom JS start when the active timer begins. HTML and JS trigger types stay blocked unless the server explicitly enables STUDY_RUNNER_ALLOW_UNSAFE_STIMULUS_CODE=1.'))}
    </p>`;
}

function renderToggleRow({ inputClass, checked, label, settings = '' }) {
  const settingsLabel = escapeHtml(t('stimulus.settingsLabel', 'Settings'));
  const rowOffClass = checked ? '' : ' stimulus-toggle-row--off';
  const settingsButton = settings
    ? `<button type="button" class="stimulus-toggle-settings" data-stimulus-settings="${escapeHtml(settings)}" title="${settingsLabel}" aria-label="${settingsLabel}" ${checked ? '' : 'disabled'}>
        <i class="iconoir-settings"></i>
      </button>`
    : '';

  return `
    <div class="stimulus-toggle-row${rowOffClass}">
      <span class="stimulus-toggle-text">${escapeHtml(label)}</span>
      <div class="stimulus-toggle-controls">
        <label class="switch" aria-label="${escapeHtml(label)}">
          <input type="checkbox" class="stimulus-toggle-input ${escapeHtml(inputClass)}" ${checked ? 'checked' : ''}>
          <span class="switch-slider"></span>
        </label>
        ${settingsButton}
      </div>
    </div>`;
}

export function bindEditorEvents(editorEl) {
  if (editorEl.dataset.stimulusBound === '1') return;
  editorEl.dataset.stimulusBound = '1';

  editorEl.addEventListener('change', (event) => {
    const toggle = event.target.closest?.('.stimulus-toggle-input');
    if (toggle) syncStimulusToggleRow(toggle.closest('.stimulus-toggle-row'));
  });

  editorEl.addEventListener('click', (event) => {
    const settingsButton = event.target.closest?.('.stimulus-toggle-settings[data-stimulus-settings="camera"]');
    if (!settingsButton || settingsButton.disabled) return;
    event.preventDefault();
    openCameraSettingsModal(editorEl);
  });
}

function syncStimulusToggleRow(row) {
  if (!row) return;
  const checked = Boolean(row.querySelector('.stimulus-toggle-input')?.checked);
  row.classList.toggle('stimulus-toggle-row--off', !checked);
  const settingsButton = row.querySelector('.stimulus-toggle-settings');
  if (settingsButton) settingsButton.disabled = !checked;
}

let _activeStimulusModal = null;

function closeStimulusModal() {
  if (!_activeStimulusModal) return;
  if (_activeStimulusModal._escHandler) document.removeEventListener('keydown', _activeStimulusModal._escHandler);
  _activeStimulusModal.remove();
  _activeStimulusModal = null;
}

function openCameraSettingsModal(editorEl) {
  closeStimulusModal();
  const intervalInput = editorEl.querySelector('.se-camera-interval');
  const currentInterval = Math.max(
    CAMERA_SNAPSHOT_MIN_INTERVAL_MS,
    Number.parseInt(intervalInput?.value || String(CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS), 10)
      || CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS,
  );

  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = `
    <div class="settings-modal" role="dialog" aria-modal="true" style="max-width: 420px;">
      <div class="settings-modal-header">
        <h2>${escapeHtml(t('stimulus.cameraSettingsTitle', 'Camera snapshot settings'))}</h2>
        <button class="overlay-close stimulus-modal-close" type="button" aria-label="${escapeHtml(t('settings.close', 'Close'))}">
          <i class="iconoir-xmark"></i>
        </button>
      </div>
      <div class="settings-modal-body">
        <div class="field">
          <label>${escapeHtml(t('stimulus.cameraIntervalLabel', 'Camera snapshot interval (ms)'))}</label>
          <input type="number" class="fi-input stimulus-modal-camera-interval" min="${CAMERA_SNAPSHOT_MIN_INTERVAL_MS}" max="${CAMERA_SNAPSHOT_MAX_INTERVAL_MS}" step="100" value="${currentInterval}">
        </div>
        <button class="btn-primary stimulus-modal-apply" type="button" style="width:100%; justify-content:center; margin-top:16px;">
          ${escapeHtml(t('settings.apply', 'Apply'))}
        </button>
      </div>
    </div>`;

  document.body.appendChild(backdrop);
  _activeStimulusModal = backdrop;

  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) closeStimulusModal();
  });
  backdrop.querySelector('.stimulus-modal-close')?.addEventListener('click', closeStimulusModal);
  backdrop.querySelector('.stimulus-modal-apply')?.addEventListener('click', () => {
    const nextValue = Number.parseInt(
      backdrop.querySelector('.stimulus-modal-camera-interval')?.value || String(CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS),
      10,
    ) || CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS;
    if (intervalInput) {
      intervalInput.value = String(Math.max(
        CAMERA_SNAPSHOT_MIN_INTERVAL_MS,
        Math.min(CAMERA_SNAPSHOT_MAX_INTERVAL_MS, nextValue),
      ));
      intervalInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
    closeStimulusModal();
  });

  backdrop._escHandler = (event) => { if (event.key === 'Escape') closeStimulusModal(); };
  document.addEventListener('keydown', backdrop._escHandler);
  backdrop.querySelector('.stimulus-modal-camera-interval')?.focus();
}

export function collectConfig(el) {
  return {
    type: 'stimulus',
    title: el.querySelector('.se-title')?.value.trim() || '',
    warmup_duration_ms: Number.parseInt(el.querySelector('.se-warmup-duration')?.value || '0', 10) * 1000,
    duration_ms: Number.parseInt(el.querySelector('.se-duration')?.value || '30', 10) * 1000,
    trigger_type: el.querySelector('.se-trigger-type')?.value || 'timer',
    trigger_content: el.querySelector('.se-trigger-content')?.value.trim() || '',
    send_signal: el.querySelector('.se-send-signal')?.checked ?? true,
    brainbit_to_lsl: true,
    brainbit_to_touchdesigner: el.querySelector('.se-brainbit-touchdesigner')?.checked ?? true,
    mini_radar_recording_enabled: el.querySelector('.se-mini-radar-recording')?.checked ?? true,
    camera_capture_enabled: el.querySelector('.se-camera-capture')?.checked ?? false,
    camera_snapshot_interval_ms: Math.max(
      CAMERA_SNAPSHOT_MIN_INTERVAL_MS,
      Number.parseInt(el.querySelector('.se-camera-interval')?.value || String(CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS), 10)
        || CAMERA_SNAPSHOT_DEFAULT_INTERVAL_MS,
    ),
  };
}

export function collectAnswer() {
  return null;
}
