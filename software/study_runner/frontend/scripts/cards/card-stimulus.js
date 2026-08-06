import { t } from '../shared/i18n.js';
import { escapeHtml } from '../shared/dom-utils.js';
import {
  PLUGIN_UI_SURFACES,
  visiblePluginsWithCapability,
} from '../shared/plugin-catalog.js';

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
  plugin_actions: {},
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
  const warmupSeconds = Math.max(0, Math.round((q.warmup_duration_ms || 0) / 1000));
  const durationSeconds = Math.max(1, Math.round((q.duration_ms || 30000) / 1000));
  const triggerType = q.trigger_type || 'timer';
  const triggerTypes = ['timer', 'image', 'video', 'audio', 'html', 'js'];
  const isContentHidden = triggerType === 'timer';
  const isCode = triggerType === 'html' || triggerType === 'js';

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
        ${renderPluginActions(q)}
      </div>
    </div>
    <p class="stimulus-editor-note">
      ${escapeHtml(t('stimulus.editorNote', 'Warm-up only shows the instruction view. Enabled plugin actions, media triggers, and custom JavaScript start with the active timer. HTML and JavaScript stay blocked unless the server explicitly enables unsafe study content.'))}
    </p>`;
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
            <span>${escapeHtml(label)}</span>
            <select class="fi-input" data-plugin-action data-plugin-key="${escapeHtml(pluginKey)}" data-action-key="${escapeHtml(actionKey)}" data-action-type="choice">
              ${(field.options || []).map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(value) ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}
            </select>
          </label>`);
        return;
      }
      const inputType = field.type === 'number' ? 'number' : 'text';
      markup.push(`
        <label class="field stimulus-plugin-action-field">
          <span>${escapeHtml(label)}</span>
          <input class="fi-input" type="${inputType}" data-plugin-action data-plugin-key="${escapeHtml(pluginKey)}" data-action-key="${escapeHtml(actionKey)}" data-action-type="${escapeHtml(field.type || 'string')}" value="${escapeHtml(value)}"${field.minimum !== undefined ? ` min="${escapeHtml(field.minimum)}"` : ''}${field.maximum !== undefined ? ` max="${escapeHtml(field.maximum)}"` : ''}>
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
  });

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
    warmup_duration_ms: Number.parseInt(el.querySelector('.se-warmup-duration')?.value || '0', 10) * 1000,
    duration_ms: Number.parseInt(el.querySelector('.se-duration')?.value || '30', 10) * 1000,
    trigger_type: el.querySelector('.se-trigger-type')?.value || 'timer',
    trigger_content: el.querySelector('.se-trigger-content')?.value.trim() || '',
    plugin_actions: pluginActions,
  };
}

function humanize(value) {
  return String(value || '').replace(/[._-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function collectAnswer() {
  return null;
}
