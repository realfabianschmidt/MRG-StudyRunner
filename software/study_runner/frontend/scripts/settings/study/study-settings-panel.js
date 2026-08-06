/**
 * The per-study settings shell, reachable only from the study editor.
 *
 * Everything in here is saved with the study and travels in the exported
 * .study-runner file: which sensors it uses, what the participant sees, and
 * where its results are uploaded. Machine-level settings (API keys, device
 * choice, timeouts) live in the gear hub instead - the two must not mix, or a
 * study copied to another computer would silently carry that computer's setup.
 *
 * The panels themselves are static markup in admin.html so the existing Notion
 * and Nextcloud controllers keep binding to the same element ids; this module
 * only owns the nav and the sensor/participant fields.
 */
import { t } from '../../shared/i18n.js';
import { byId, escapeHtml, setText } from '../../shared/dom-utils.js';
import { activateShellPanel, bindShellNav, renderShellNav } from '../../shared/settings-shell.js';
import { normalizeStudySettings } from '../../shared/study-settings.js';
import {
  PLUGIN_UI_SURFACES,
  visiblePluginsWithCapability,
} from '../../shared/plugin-catalog.js';

let callbacks = {};
let initialized = false;
let activePanel = 'sensors';

export function initializeStudySettingsPanel(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;

  byId('btn-study-settings-back')?.addEventListener('click', () => void callbacks.switchView?.('view-workspace'));
  byId('study-sensors-enabled')?.addEventListener('change', syncSensorControls);
  byId('study-plugin-sensor-options')?.addEventListener('change', (event) => {
    if (event.target?.matches('[data-plugin-enabled]')) syncSensorControls();
  });
  byId('btn-save-study-settings')?.addEventListener('click', () => void saveFromPanel());
  byId('btn-save-participant-settings')?.addEventListener('click', () => void saveFromPanel());
  byId('btn-save-plugin-destinations')?.addEventListener('click', () => void saveDestinationPlugins());
  byId('btn-study-settings-download')?.addEventListener('click', () => callbacks.downloadCurrentStudy?.());
}

/** Open the panel, optionally jumping straight to one section. */
export function openStudySettingsPanel(panelKey) {
  if (panelKey) activePanel = panelKey;
  return callbacks.switchView?.('view-study-settings', { onCovered: renderStudySettingsPanel });
}

export function renderStudySettingsPanel() {
  const nav = byId('study-settings-nav');
  const root = byId('view-study-settings');
  if (!nav || !root) return;

  nav.innerHTML = renderShellNav(studySettingsEntries(), activePanel);
  activePanel = activateShellPanel(root, activePanel);
  // Switching panels must refresh too, not just opening the shell: the Notion
  // and Nextcloud fields are filled by their own controllers, and a panel the
  // operator has not visited yet still holds the previous study's values.
  bindShellNav(root, (key) => {
    activePanel = key;
  });

  setText('study-settings-heading', callbacks.getCurrentStudyName?.() || t('studySettings.title', 'Study settings'));
  fillFields();
}

function studySettingsEntries() {
  return [
    { key: 'sensors', icon: 'iconoir-activity', label: t('studySettings.navSensors', 'Sensors'), group: t('studySettings.groupRecording', 'Recording') },
    { key: 'participant', icon: 'iconoir-smartphone-device', label: t('studySettings.navParticipant', 'Participant experience'), group: t('studySettings.groupRecording', 'Recording') },
    { key: 'destinations', icon: 'iconoir-cloud-upload', label: t('studySettings.navDestinations', 'Data destinations'), group: t('studySettings.groupUploads', 'Uploads') },
    { key: 'export', icon: 'iconoir-download', label: t('studySettings.navExport', 'Export & privacy'), group: t('studySettings.groupFile', 'Study file') },
  ];
}

function fillFields() {
  const settings = normalizeStudySettings(callbacks.getStudyConfig?.().study_settings);
  const set = (id, value) => { const el = byId(id); if (el) el.checked = Boolean(value); };
  set('study-sensors-enabled', settings.sensors_enabled !== false);
  renderSensorPlugins(settings);
  renderDestinationPlugins(settings);
  set('study-progress-bar-enabled', settings.progress_bar_enabled);
  syncSensorControls();
}

function renderDestinationPlugins(settings) {
  const container = byId('study-plugin-destination-options');
  if (!container) return;
  const plugins = visiblePluginsWithCapability(
    'upload_destination',
    PLUGIN_UI_SURFACES.DESTINATION_SETTINGS,
  );
  if (!plugins.length) {
    container.innerHTML = `<p class="settings-hint">${escapeHtml(t('studySettings.noDestinationPlugins', 'No upload destination plugins are installed.'))}</p>`;
    return;
  }
  container.innerHTML = plugins.map((plugin) => {
    const key = String(plugin.plugin_key);
    const configured = settings.plugins?.[key] || {};
    const enabled = configured.enabled === true;
    const schema = plugin.study_settings_schema || plugin.settings?.study || {};
    const hasSpecialSettings = Boolean(destinationSettingsTrigger(key));
    return `
      <div class="stimulus-toggle-row study-plugin-row${enabled ? '' : ' stimulus-toggle-row--off'}" data-study-destination="${escapeHtml(key)}">
        <div class="study-plugin-main">
          <span class="stimulus-toggle-text"><strong>${escapeHtml(plugin.ui?.label || key)}</strong>${plugin.ui?.description ? `<small>${escapeHtml(plugin.ui.description)}</small>` : ''}</span>
          <label class="switch" aria-label="${escapeHtml(plugin.ui?.label || key)}"><input type="checkbox" data-plugin-enabled ${enabled ? 'checked' : ''}><span class="switch-slider"></span></label>
        </div>
        ${renderPluginStudyFields(key, schema, configured.settings || {})}
        ${hasSpecialSettings ? `
          <div class="dashboard-actions">
            <button class="btn-secondary" type="button" data-plugin-special-settings="${escapeHtml(key)}">
              <i class="iconoir-settings"></i> <span>${escapeHtml(t('studySettings.destinationSpecialSettings', 'Credentials & connection'))}</span>
            </button>
          </div>` : ''}
      </div>`;
  }).join('');
  container.querySelectorAll('[data-plugin-enabled]').forEach((input) => {
    input.addEventListener('change', syncDestinationControls);
  });
  container.querySelectorAll('[data-plugin-special-settings]').forEach((button) => {
    button.addEventListener('click', () => {
      destinationSettingsTrigger(button.dataset.pluginSpecialSettings)?.click();
    });
  });
  syncDestinationControls();
}

/**
 * Optional rich destination UIs use one deliberately boring convention.
 *
 * The plugin-owned markup/controller may expose `btn-<plugin-key>-settings`.
 * Its presence adds an action beside the generic schema form; the generic
 * catalog UI neither knows the plugin key nor how that special page works.
 */
function destinationSettingsTrigger(pluginKey) {
  return byId(`btn-${String(pluginKey || '')}-settings`);
}

function syncDestinationControls() {
  byId('study-plugin-destination-options')?.querySelectorAll('[data-study-destination]').forEach((row) => {
    const enabled = Boolean(row.querySelector('[data-plugin-enabled]')?.checked);
    row.classList.toggle('stimulus-toggle-row--off', !enabled);
    row.querySelectorAll('[data-plugin-setting]').forEach((input) => { input.disabled = !enabled; });
  });
}

function renderSensorPlugins(settings) {
  const container = byId('study-plugin-sensor-options');
  if (!container) return;
  const plugins = visiblePluginsWithCapability('study_sensor', PLUGIN_UI_SURFACES.STUDY_SETTINGS);
  if (!plugins.length) {
    container.innerHTML = `<p class="settings-hint">${escapeHtml(t('studySettings.noSensorPlugins', 'No valid sensor plugins are installed.'))}</p>`;
    return;
  }
  container.innerHTML = plugins.map((plugin) => renderSensorPlugin(plugin, settings)).join('');
}

function renderSensorPlugin(plugin, settings) {
  const key = String(plugin.plugin_key);
  const capability = plugin.capability_config?.study_sensor || {};
  const configured = settings.plugins?.[key] || {};
  const legacySelected = settings.sensors?.[key];
  const enabled = configured.enabled ?? legacySelected ?? capability.default_enabled ?? false;
  const required = configured.required ?? capability.default_required ?? true;
  const label = plugin.ui?.label || key;
  const description = plugin.ui?.description || '';
  const studySchema = plugin.study_settings_schema || plugin.settings?.study || {};
  return `
    <div class="stimulus-toggle-row study-plugin-row${enabled ? '' : ' stimulus-toggle-row--off'}" data-study-plugin="${escapeHtml(key)}">
      <div class="study-plugin-main">
        <span class="stimulus-toggle-text"><strong>${escapeHtml(label)}</strong>${description ? `<small>${escapeHtml(description)}</small>` : ''}</span>
        <label class="switch" aria-label="${escapeHtml(label)}"><input type="checkbox" data-plugin-enabled ${enabled ? 'checked' : ''}><span class="switch-slider"></span></label>
      </div>
      <div class="study-plugin-required">
        <span>${escapeHtml(t('studySettings.requiredSensor', 'Required at study start'))}</span>
        <label class="switch" aria-label="${escapeHtml(t('studySettings.requiredSensor', 'Required at study start'))}"><input type="checkbox" data-plugin-required ${required ? 'checked' : ''}><span class="switch-slider"></span></label>
      </div>
      ${renderPluginStudyFields(key, studySchema, configured.settings || {})}
    </div>`;
}

function renderPluginStudyFields(pluginKey, schema, values) {
  const entries = Object.entries(schema || {});
  if (!entries.length) return '';
  return `<div class="study-plugin-fields">${entries.map(([name, field]) => {
    const value = values[name] ?? field.default ?? '';
    const label = field.label_key ? t(field.label_key, field.label || humanize(name)) : field.label || humanize(name);
    const hint = field.description_key
      ? t(field.description_key, field.description || '')
      : field.description || '';
    const disabled = field.read_only === true ? ' disabled' : '';
    const required = field.required === true ? ' required' : '';
    if (field.type === 'boolean') {
      return `<label class="switch-row"><span>${escapeHtml(label)}${hint ? `<small>${escapeHtml(hint)}</small>` : ''}</span><span class="switch"><input type="checkbox" data-plugin-setting="${escapeHtml(name)}" data-setting-type="boolean" ${value ? 'checked' : ''}${disabled}><span class="switch-slider"></span></span></label>`;
    }
    if (field.type === 'choice') {
      return `<label class="field"><span>${escapeHtml(label)}</span><select class="fi-input" data-plugin-setting="${escapeHtml(name)}" data-setting-type="choice"${disabled}${required}>${(field.options || []).map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(value) ? 'selected' : ''}>${escapeHtml(option)}</option>`).join('')}</select>${hint ? `<small class="settings-hint">${escapeHtml(hint)}</small>` : ''}</label>`;
    }
    const inputType = ['number', 'url'].includes(field.type) ? field.type : 'text';
    const limits = field.type === 'number'
      ? `${field.minimum !== undefined ? ` min="${escapeHtml(field.minimum)}"` : ''}${field.maximum !== undefined ? ` max="${escapeHtml(field.maximum)}"` : ''}`
      : '';
    const placeholder = field.placeholder ? ` placeholder="${escapeHtml(field.placeholder)}"` : '';
    const unit = field.unit ? ` <small>${escapeHtml(field.unit)}</small>` : '';
    return `<label class="field"><span>${escapeHtml(label)}${unit}</span><input class="fi-input" type="${inputType}" data-plugin-setting="${escapeHtml(name)}" data-setting-type="${escapeHtml(field.type || 'string')}" value="${escapeHtml(value)}"${limits}${placeholder}${disabled}${required}>${hint ? `<small class="settings-hint">${escapeHtml(hint)}</small>` : ''}</label>`;
  }).join('')}</div>`;
}

function syncSensorControls() {
  const enabled = Boolean(byId('study-sensors-enabled')?.checked);
  byId('study-plugin-sensor-options')?.querySelectorAll('[data-study-plugin]').forEach((row) => {
    const pluginEnabled = enabled && Boolean(row.querySelector('[data-plugin-enabled]')?.checked);
    row.classList.toggle('stimulus-toggle-row--off', !pluginEnabled);
    row.querySelectorAll('input, select').forEach((input) => {
      input.disabled = !enabled || (input.matches('[data-plugin-required], [data-plugin-setting]') && !pluginEnabled);
    });
  });
  byId('study-sensor-options')?.classList.toggle('is-disabled', !enabled);
}

/** Collect the fields this module owns and hand the save to the editor. */
async function saveFromPanel() {
  const sensorsEnabled = Boolean(byId('study-sensors-enabled')?.checked);
  const current = normalizeStudySettings(callbacks.getStudyConfig?.().study_settings);
  const plugins = { ...current.plugins };
  const sensors = {};
  byId('study-plugin-sensor-options')?.querySelectorAll('[data-study-plugin]').forEach((row) => {
    const pluginKey = row.dataset.studyPlugin;
    const enabled = sensorsEnabled && Boolean(row.querySelector('[data-plugin-enabled]')?.checked);
    const previous = plugins[pluginKey] || {};
    const pluginSettings = { ...(previous.settings || {}) };
    row.querySelectorAll('[data-plugin-setting]').forEach((input) => {
      const name = input.dataset.pluginSetting;
      const type = input.dataset.settingType;
      pluginSettings[name] = type === 'boolean'
        ? Boolean(input.checked)
        : type === 'number'
          ? Number(input.value)
          : input.value;
    });
    sensors[pluginKey] = enabled;
    plugins[pluginKey] = {
      enabled,
      required: enabled && Boolean(row.querySelector('[data-plugin-required]')?.checked),
      settings: pluginSettings,
    };
  });
  // Spread the existing settings first: this panel edits sensors and the
  // progress bar, and must never drop the upload settings the other panels own.
  callbacks.setStudySettings?.({
    ...current,
    sensors_enabled: sensorsEnabled,
    sensors,
    plugins,
    progress_bar_enabled: Boolean(byId('study-progress-bar-enabled')?.checked),
  });
  await callbacks.saveStudyConfig?.({
    successMessage: t('studySettings.saved', 'Study settings saved'),
  });
}

async function saveDestinationPlugins() {
  const current = normalizeStudySettings(callbacks.getStudyConfig?.().study_settings);
  const plugins = { ...current.plugins };
  byId('study-plugin-destination-options')?.querySelectorAll('[data-study-destination]').forEach((row) => {
    const pluginKey = row.dataset.studyDestination;
    const previous = plugins[pluginKey] || {};
    const pluginSettings = { ...(previous.settings || {}) };
    row.querySelectorAll('[data-plugin-setting]').forEach((input) => {
      const type = input.dataset.settingType;
      pluginSettings[input.dataset.pluginSetting] = type === 'boolean'
        ? Boolean(input.checked)
        : type === 'number'
          ? Number(input.value)
          : input.value;
    });
    plugins[pluginKey] = {
      enabled: Boolean(row.querySelector('[data-plugin-enabled]')?.checked),
      required: false,
      settings: pluginSettings,
    };
  });
  callbacks.setStudySettings?.({ ...current, plugins });
  await callbacks.saveStudyConfig?.({ successMessage: t('studySettings.saved', 'Study settings saved') });
}

function humanize(value) {
  return String(value || '').replace(/[._-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}
