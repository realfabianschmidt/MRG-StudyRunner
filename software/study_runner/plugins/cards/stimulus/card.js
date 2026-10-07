import { t } from '/static/scripts/shared/i18n.js';
import { escapeHtml } from '/static/scripts/shared/dom-utils.js';
import {
  PLUGIN_UI_SURFACES,
  stimulusActuatorPlugins,
  visiblePluginsWithCapability,
} from '/static/scripts/shared/plugin-catalog.js';
import { renderEditorToggle } from '/static/scripts/cards/card-info.js';
import { installSoundCueUnlock, playSoundCue, preloadSoundCue } from './sound-cues.js';

const END_SOUNDS = ['none', 'gong', 'bell', 'beep', 'custom'];

export const meta = {
  type: 'stimulus',
  icon: 'timer',
  label: 'Stimulus / Countdown',
  suppressSharedInfoTop: true,
};


export function renderStudy(q, i) {
  // Audio needs a user gesture first; the participant's next tap provides it.
  installSoundCueUnlock();
  const warmupSeconds = Math.max(0, Math.round((q.warmup_duration_ms ?? defaultQuestion.warmup_duration_ms) / 1000));
  const durationSeconds = Math.max(1, Math.round((q.duration_ms ?? defaultQuestion.duration_ms) / 1000));
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
          <h1 class="stimulus-hero-title">${escapeHtml(q.title ?? defaultQuestion.title)}</h1>
          <p class="stimulus-hero-sub">${escapeHtml(q.info_top ?? q.subtitle ?? defaultQuestion.info_top ?? '')}</p>
        </div>
        <div class="stimulus-mini-timer" id="stimulus-mini-timer-${i}">
          <span class="stimulus-mini-label">${escapeHtml(t('stimulus.startsIn', 'Starts in'))}</span>
          <span class="stimulus-mini-value" id="warmup-num-${i}">${warmupSeconds}</span>
        </div>
      </div>

      <div class="stimulus-stage stimulus-stage--active" id="stimulus-active-${i}"${startsWithWarmup ? ' hidden' : ''}>
        <div class="q-type-tag"><i class="iconoir-timer"></i> ${escapeHtml(t('stimulus.active', 'Stimulus active'))}</div>
        <div class="stimulus-active-copy">
          <h1 class="screen-title">${escapeHtml(q.title ?? defaultQuestion.title)}</h1>
          <p class="screen-sub">${escapeHtml(q.info_top ?? q.subtitle ?? defaultQuestion.info_top ?? '')}</p>
        </div>
        <div class="stimulus-content" id="stimulus-content-${i}" hidden></div>
        <svg class="cd-ring" viewBox="0 0 120 120" aria-hidden="true">
          <circle cx="60" cy="60" r="50" fill="none" stroke="var(--ink-08)" stroke-width="5"></circle>
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
  const warmupSeconds = Math.max(0, Math.round((q.warmup_duration_ms ?? defaultQuestion.warmup_duration_ms) / 1000));
  const durationSeconds = Math.max(1, Math.round((q.duration_ms ?? defaultQuestion.duration_ms) / 1000));
  const triggerType = q.trigger_type ?? defaultQuestion.trigger_type;
  const triggerTypes = ['timer', 'image', 'video', 'audio', 'html', 'js'];
  const isContentHidden = triggerType === 'timer';
  const isCode = triggerType === 'html' || triggerType === 'js';

  return `
    <div class="field">
      <label>${escapeHtml(t('stimulus.titleLabel', 'Title'))}</label>
      <input type="text" class="se-title" value="${escapeHtml(q.title ?? defaultQuestion.title)}">
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
            ${escapeHtml(t(`stimulus.trigger.${type}`, type.toUpperCase()))}
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
      <label>${escapeHtml(t('stimulus.actuatorsLabel', 'Control actuators (start/stop)'))}</label>
      <div class="stimulus-toggle-list">
        ${renderActuatorSelection(q)}
      </div>
      <p class="settings-hint">${escapeHtml(t('stimulus.actuatorsHint', 'Sensors always record continuously and only receive markers.'))}</p>
    </div>
    <div class="field">
      <label>${escapeHtml(t('stimulus.signalSettingsLabel', 'Plugin settings for this card'))}</label>
      <div class="stimulus-toggle-list">
        ${renderPluginActions(q)}
      </div>
    </div>
    ${renderEndOfTime(q)}
    <p class="stimulus-editor-note">
      ${escapeHtml(t('stimulus.editorNote', 'Warm-up only shows the instruction view. Selected actuators, media triggers, and custom JavaScript start with the active timer. HTML and JavaScript stay blocked unless the server explicitly enables unsafe study content.'))}
    </p>`;
}

function renderActuatorSelection(question) {
  const actuators = stimulusActuatorPlugins();
  if (!actuators.length) {
    return `<p class="settings-hint">${escapeHtml(t('stimulus.noActuators', 'No installed plugin can drive actuators.'))}</p>`;
  }
  const selected = new Set(question.actuator_plugins ?? defaultQuestion.actuator_plugins ?? []);
  return actuators.map((plugin) => {
    const pluginKey = plugin.plugin_key;
    const checked = selected.has(pluginKey);
    const label = plugin.ui?.label || pluginKey;
    return `
      <div class="stimulus-toggle-row${checked ? '' : ' stimulus-toggle-row--off'}">
        <span class="stimulus-toggle-text">${escapeHtml(label)}</span>
        <div class="stimulus-toggle-controls">
          <label class="switch" aria-label="${escapeHtml(label)}">
            <input type="checkbox" class="stimulus-toggle-input se-actuator" data-plugin-key="${escapeHtml(pluginKey)}" ${checked ? 'checked' : ''}>
            <span class="switch-slider"></span>
          </label>
        </div>
      </div>`;
  }).join('');
}

function renderEndOfTime(question) {
  const value = (key) => question[key] ?? defaultQuestion[key];
  const endSound = value('end_sound');
  const autoAdvance = value('auto_advance') !== false;
  const maxOvertimeSeconds = Math.round(value('overtime_max_ms') / 1000);
  return `
    <div class="field stimulus-end-block">
      <label>${escapeHtml(t('stimulus.endOfTimeLabel', 'When the time is up'))}</label>
      <div class="row2">
        <div class="field">
          <label>${escapeHtml(t('stimulus.endSoundLabel', 'Sound'))}</label>
          <select class="se-end-sound">
            ${END_SOUNDS.map((sound) => `<option value="${sound}" ${sound === endSound ? 'selected' : ''}>${escapeHtml(t(`stimulus.endSound.${sound}`, sound))}</option>`).join('')}
          </select>
        </div>
        <div class="field">
          <label>${escapeHtml(t('stimulus.endSoundVolumeLabel', 'Volume (%)'))}</label>
          <input type="number" class="se-end-sound-volume" min="0" max="100" value="${escapeHtml(value('end_sound_volume'))}">
        </div>
      </div>
      <div class="field se-end-sound-url-field"${endSound === 'custom' ? '' : ' hidden'}>
        <label>${escapeHtml(t('stimulus.endSoundUrlLabel', 'Sound file (URL)'))}</label>
        <input type="url" class="se-end-sound-url" placeholder="${escapeHtml(t('stimulus.urlPlaceholder', 'https://...'))}" value="${escapeHtml(value('end_sound_url'))}">
      </div>
      <button type="button" class="btn-secondary se-end-sound-preview"${endSound === 'none' ? ' disabled' : ''}>
        <i class="iconoir-sound-high"></i> ${escapeHtml(t('stimulus.endSoundPreview', 'Play sound'))}
      </button>
      <div class="editor-toggles">
        ${renderEditorToggle({
          className: 'se-auto-advance',
          checked: autoAdvance,
          label: t('stimulus.autoAdvanceLabel', 'Continue to the next card automatically'),
          title: t('stimulus.autoAdvanceHint', 'Off: the duration becomes a minimum. The card stays, Next becomes available, and the time until Next is saved as overtime.'),
        })}
      </div>
      <div class="se-overtime-fields"${autoAdvance ? ' hidden' : ''}>
        <div class="editor-toggles">
          ${renderEditorToggle({
            className: 'se-overtime-keep-stimulus',
            checked: value('overtime_keep_stimulus') !== false,
            label: t('stimulus.overtimeKeepStimulusLabel', 'Stimulus keeps running after the time is up'),
            title: t('stimulus.overtimeKeepStimulusHint', 'Image, video, audio, HTML and JavaScript stay until the participant taps Next.'),
          })}
          ${renderEditorToggle({
            className: 'se-overtime-keep-actuators',
            checked: value('overtime_keep_actuators') === true,
            label: t('stimulus.overtimeKeepActuatorsLabel', 'Actuators keep running during overtime'),
            title: t('stimulus.overtimeKeepActuatorsHint', 'On: the selected actuators receive stop only when the participant taps Next. Off: they stop when the time is up.'),
          })}
        </div>
        <div class="field">
          <label>${escapeHtml(t('stimulus.overtimeMaxLabel', 'Maximum overtime (seconds)'))}</label>
          <input type="number" class="se-overtime-max" min="10" max="3600" value="${escapeHtml(maxOvertimeSeconds)}">
        </div>
        <p class="settings-hint">${escapeHtml(t('stimulus.overtimeHint', 'After the maximum the card continues by itself.'))}</p>
      </div>
    </div>`;
}

function renderToggleRow({ checked, label, pluginKey, actionKey }) {
  const rowOffClass = checked ? '' : ' stimulus-toggle-row--off';

  return `
    <div class="stimulus-toggle-row${rowOffClass}">
      <span class="stimulus-toggle-text">${escapeHtml(label)}</span>
      <div class="stimulus-toggle-controls">
        <label class="switch" aria-label="${escapeHtml(label)}">
          <input type="checkbox" class="stimulus-toggle-input" data-plugin-action data-plugin-key="${escapeHtml(pluginKey)}" data-action-key="${escapeHtml(actionKey)}" data-action-type="boolean" ${checked ? 'checked' : ''}>
          <span class="switch-slider"></span>
        </label>
      </div>
    </div>`;
}

function renderPluginActions(question) {
  const plugins = visiblePluginsWithCapability('card_actions', PLUGIN_UI_SURFACES.STUDY_SETTINGS);
  const markup = [];
  plugins.forEach((plugin) => {
    const pluginKey = plugin.plugin_key;
    const schema = plugin.card_actions_schema || plugin.settings?.card_actions || {};
    Object.entries(schema).forEach(([actionKey, field]) => {
      const value = question.plugin_actions?.[pluginKey]?.[actionKey] ?? field.default ?? null;
      const fallbackLabel = field.label || `${plugin.ui?.label || pluginKey}: ${humanize(actionKey)}`;
      const label = field.label_key ? t(field.label_key, fallbackLabel) : fallbackLabel;
      if (field.type === 'boolean') {
        markup.push(renderToggleRow({
          checked: Boolean(value),
          label,
          pluginKey,
          actionKey,
        }));
        return;
      }
      if (field.type === 'choice') {
        markup.push(`
          <label class="field stimulus-plugin-action-field">
            <span>${escapeHtml(label)}${field.unit ? ` <span class="settings-unit">(${escapeHtml(field.unit)})</span>` : ''}</span>
            <select data-plugin-action data-plugin-key="${escapeHtml(pluginKey)}" data-action-key="${escapeHtml(actionKey)}" data-action-type="choice">
              ${(field.options || []).map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(value) ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}
            </select>
          </label>`);
        return;
      }
      const inputType = field.type === 'number' ? 'number' : 'text';
      markup.push(`
        <label class="field stimulus-plugin-action-field">
          <span>${escapeHtml(label)}${field.unit ? ` <span class="settings-unit">(${escapeHtml(field.unit)})</span>` : ''}</span>
          <input type="${inputType}" data-plugin-action data-plugin-key="${escapeHtml(pluginKey)}" data-action-key="${escapeHtml(actionKey)}" data-action-type="${escapeHtml(field.type || 'string')}" value="${escapeHtml(value)}"${field.minimum !== undefined ? ` min="${escapeHtml(field.minimum)}"` : ''}${field.maximum !== undefined ? ` max="${escapeHtml(field.maximum)}"` : ''}>
        </label>`);
    });
  });
  return markup.length
    ? markup.join('')
    : `<p class="settings-hint">${escapeHtml(t('stimulus.noPluginActions', 'No plugin actions are available.'))}</p>`;
}

export function bindEditorEvents(editorEl) {
  if (editorEl.dataset.stimulusBound === '1') return;
  editorEl.dataset.stimulusBound = '1';

  editorEl.addEventListener('change', (event) => {
    const toggle = event.target.closest?.('.stimulus-toggle-input');
    if (toggle) syncStimulusToggleRow(toggle.closest('.stimulus-toggle-row'));
    if (event.target.closest?.('.se-end-sound, .se-auto-advance')) syncEndOfTimeFields(editorEl);
  });

  editorEl.addEventListener('click', (event) => {
    if (!event.target.closest?.('.se-end-sound-preview')) return;
    void playSoundCue(collectEndSound(editorEl));
  });
}

function syncEndOfTimeFields(editorEl) {
  const endSound = editorEl.querySelector('.se-end-sound')?.value || 'none';
  const urlField = editorEl.querySelector('.se-end-sound-url-field');
  if (urlField) urlField.hidden = endSound !== 'custom';
  const preview = editorEl.querySelector('.se-end-sound-preview');
  if (preview) preview.disabled = endSound === 'none';
  const overtimeFields = editorEl.querySelector('.se-overtime-fields');
  if (overtimeFields) overtimeFields.hidden = editorEl.querySelector('.se-auto-advance')?.checked !== false;
}

function collectEndSound(el) {
  return {
    end_sound: el.querySelector('.se-end-sound')?.value || defaultQuestion.end_sound,
    end_sound_url: el.querySelector('.se-end-sound-url')?.value.trim() || '',
    end_sound_volume: readInteger(el, '.se-end-sound-volume', defaultQuestion.end_sound_volume),
  };
}

function readInteger(el, selector, fallback) {
  const value = Number.parseInt(el.querySelector(selector)?.value ?? '', 10);
  return Number.isFinite(value) ? value : fallback;
}

function syncStimulusToggleRow(row) {
  if (!row) return;
  const checked = Boolean(row.querySelector('.stimulus-toggle-input')?.checked);
  row.classList.toggle('stimulus-toggle-row--off', !checked);
}

export function collectConfig(el) {
  const pluginActions = {};
  el.querySelectorAll('[data-plugin-action]').forEach((input) => {
    const pluginKey = input.dataset.pluginKey;
    const actionKey = input.dataset.actionKey;
    if (!pluginKey || !actionKey) return;
    pluginActions[pluginKey] ||= {};
    pluginActions[pluginKey][actionKey] = input.dataset.actionType === 'boolean'
      ? Boolean(input.checked)
      : input.dataset.actionType === 'number'
        ? Number(input.value)
        : input.value;
  });
  return {
    type: 'stimulus',
    title: el.querySelector('.se-title')?.value.trim() || '',
    warmup_duration_ms: Number.parseInt(el.querySelector('.se-warmup-duration')?.value || String(defaultQuestion.warmup_duration_ms / 1000), 10) * 1000,
    duration_ms: Number.parseInt(el.querySelector('.se-duration')?.value || String(defaultQuestion.duration_ms / 1000), 10) * 1000,
    trigger_type: el.querySelector('.se-trigger-type')?.value || defaultQuestion.trigger_type,
    trigger_content: el.querySelector('.se-trigger-content')?.value.trim() || '',
    plugin_actions: pluginActions,
    actuator_plugins: [...el.querySelectorAll('.se-actuator')]
      .filter((input) => input.checked)
      .map((input) => input.dataset.pluginKey),
    ...collectEndSound(el),
    auto_advance: el.querySelector('.se-auto-advance')?.checked !== false,
    overtime_keep_stimulus: el.querySelector('.se-overtime-keep-stimulus')?.checked !== false,
    overtime_keep_actuators: el.querySelector('.se-overtime-keep-actuators')?.checked === true,
    overtime_max_ms: readInteger(el, '.se-overtime-max', defaultQuestion.overtime_max_ms / 1000) * 1000,
  };
}

function humanize(value) {
  return String(value || '').replace(/[._-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function collectAnswer() {
  return null;
}

/**
 * Participant-side hooks the stimulus runtime calls by name. The runtime owns
 * timing, navigation and trial events; the sound is this card's business.
 */
export function onStimulusPrepared(question) {
  void preloadSoundCue(question);
}

export function onTimeUp(question, index, { notify } = {}) {
  return playSoundCue(question, {
    onFallback: (url) => notify?.(
      t('stimulus.endSoundFallback', 'The end sound file could not be loaded; the built-in gong was played instead: {url}').replace('{url}', url),
      'warning',
    ),
    onBlocked: () => notify?.(
      t('stimulus.endSoundBlocked', 'The tablet browser blocked the end sound. Tap the screen once before the next stimulus.'),
      'warning',
    ),
  });
}

export let defaultQuestion;
export function configureCard(defaults) { defaultQuestion = defaults['stimulus']; }
meta.hideSharedPrompt = true;
export const metaByType = { 'stimulus': meta };
