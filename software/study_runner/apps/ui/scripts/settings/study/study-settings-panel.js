/**
 * The per-study settings shell, reachable only from the study editor.
 *
 * Everything in here is saved with the study and travels in the exported
 * .study-runner file: which sensors it uses, what the participant sees, and
 * where its results are uploaded. Machine-level settings (API keys, device
 * choice, timeouts) live in the gear hub instead - the two must not mix, or a
 * study copied to another computer would silently carry that computer's setup.
 *
 * Core panels provide only the shell. Plugin settings, credentials and admin
 * actions are generated from the catalog, so removing a bundle removes its UI
 * without leaving dormant buttons behind.
 */
import { t } from '../../shared/i18n.js';
import { byId, escapeHtml, setText } from '../../shared/dom-utils.js';
import { getJson, postJson } from '../../shared/api-client.js';
import { activateShellPanel, bindShellNav, renderShellNav } from '../../shared/settings-shell.js';
import { normalizeStudySettings } from '../../shared/study-settings.js';
import {
  PLUGIN_UI_SURFACES,
  getPluginCatalog,
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
  byId('study-plugin-sensor-options')?.addEventListener('click', (event) => {
    const remove = event.target?.closest?.('[data-remove-unavailable-plugin]');
    if (remove) void removeUnavailablePlugin(remove.dataset.removeUnavailablePlugin || '');
  });
  byId('study-plugin-destination-options')?.addEventListener('click', (event) => {
    const saveCredential = event.target?.closest?.('[data-save-study-plugin-credential]');
    if (saveCredential) {
      void saveStudyPluginCredential(saveCredential.dataset.saveStudyPluginCredential || '');
      return;
    }
    const clearCredential = event.target?.closest?.('[data-clear-study-plugin-credential]');
    if (clearCredential) {
      void clearStudyPluginCredential(clearCredential.dataset.clearStudyPluginCredential || '');
      return;
    }
    const action = event.target?.closest?.('[data-study-plugin-action]');
    if (action) void runStudyPluginAction(action);
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
  // Switching panels only changes visibility; plugin fields are rebuilt from
  // the currently loaded study every time the shell opens.
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
  void refreshStudyPluginCredentialStates();
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
    return `
      <div class="stimulus-toggle-row study-plugin-row${enabled ? '' : ' stimulus-toggle-row--off'}" data-study-destination="${escapeHtml(key)}">
        <div class="study-plugin-main">
          <span class="stimulus-toggle-text"><strong>${escapeHtml(plugin.ui?.label || key)}</strong>${plugin.ui?.description ? `<small>${escapeHtml(plugin.ui.description)}</small>` : ''}</span>
          <label class="switch" aria-label="${escapeHtml(plugin.ui?.label || key)}"><input type="checkbox" data-plugin-enabled ${enabled ? 'checked' : ''}><span class="switch-slider"></span></label>
        </div>
        ${renderPluginStudyFields(key, schema, configured.settings || {})}
        ${renderStudyPluginCredential(plugin)}
        ${renderStudyPluginActions(plugin, schema)}
      </div>`;
  }).join('');
  container.querySelectorAll('[data-plugin-enabled]').forEach((input) => {
    input.addEventListener('change', syncDestinationControls);
  });
  syncDestinationControls();
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
  const unavailable = unavailablePluginSelections(settings);
  if (!plugins.length && !unavailable.length) {
    container.innerHTML = `<p class="settings-hint">${escapeHtml(t('studySettings.noSensorPlugins', 'No valid sensor plugins are installed.'))}</p>`;
    return;
  }
  container.innerHTML = [
    ...plugins.map((plugin) => renderSensorPlugin(plugin, settings)),
    ...unavailable.map(([pluginKey, selection]) => renderUnavailablePlugin(pluginKey, selection)),
  ].join('');
}

function unavailablePluginSelections(settings) {
  const installed = new Set(getPluginCatalog().plugins.map((plugin) => String(plugin.plugin_key)));
  return Object.entries(settings.plugins || {})
    .filter(([pluginKey, selection]) => (
      !installed.has(pluginKey)
      && selection
      && typeof selection === 'object'
      && !Array.isArray(selection)
    ));
}

function renderUnavailablePlugin(pluginKey, selection) {
  const enabled = selection.enabled === true;
  const required = enabled && selection.required === true;
  const state = required
    ? t('studySettings.unavailableRequired', 'Required and enabled: study start is blocked')
    : enabled
      ? t('studySettings.unavailableOptional', 'Enabled but unavailable: results may be incomplete')
      : t('studySettings.unavailableDisabled', 'Unavailable and disabled');
  return `
    <div class="stimulus-toggle-row study-plugin-row study-plugin-row--unavailable" data-unavailable-plugin="${escapeHtml(pluginKey)}">
      <div class="study-plugin-main">
        <span class="stimulus-toggle-text">
          <strong>${escapeHtml(pluginKey)}</strong>
          <small>${escapeHtml(t('studySettings.pluginUnavailable', 'This study references a plugin that is not installed. Its settings and card actions are preserved unchanged.'))}</small>
          <small>${escapeHtml(state)}</small>
        </span>
        <i class="iconoir-warning-triangle" aria-hidden="true"></i>
      </div>
      <div class="dashboard-actions">
        <button class="btn-secondary" type="button" data-remove-unavailable-plugin="${escapeHtml(pluginKey)}">
          <i class="iconoir-trash"></i> ${escapeHtml(t('studySettings.removeUnavailablePlugin', 'Remove plugin configuration'))}
        </button>
      </div>
    </div>`;
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

function renderStudyPluginCredential(plugin) {
  const credential = plugin.capability_config?.credentials || {};
  if (!credential.config_field || credential.per_study !== true) return '';
  const pluginKey = String(plugin.plugin_key);
  const label = humanize(credential.config_field);
  return `
    <div class="study-plugin-credential" data-study-plugin-credential="${escapeHtml(pluginKey)}">
      <label class="field">
        <span>${escapeHtml(label)}</span>
        <input class="fi-input" type="password" autocomplete="new-password" data-study-plugin-credential-input>
        <small class="settings-hint" data-study-plugin-credential-state>${escapeHtml(t('studySettings.credentialLoading', 'Checking saved credential...'))}</small>
      </label>
      <div class="dashboard-actions">
        <button class="btn-secondary" type="button" data-save-study-plugin-credential="${escapeHtml(pluginKey)}">
          <i class="iconoir-key"></i> ${escapeHtml(t('studySettings.saveCredential', 'Save credential for this study'))}
        </button>
        <button class="btn-secondary" type="button" data-clear-study-plugin-credential="${escapeHtml(pluginKey)}">
          <i class="iconoir-trash"></i> ${escapeHtml(t('studySettings.clearCredential', "Delete this study's credential"))}
        </button>
      </div>
      <small class="settings-hint">${escapeHtml(t('studySettings.credentialPrivateHint', 'Credentials stay in the local secret store and are never written to the study file.'))}</small>
    </div>`;
}

function renderStudyPluginActions(plugin, studySchema) {
  const actions = plugin.capability_config?.admin_actions?.actions || [];
  if (!actions.length) return '';
  const credentialField = String(plugin.capability_config?.credentials?.config_field || '');
  return `<div class="study-plugin-actions">${actions.map((action) => {
    const additionalFields = Object.entries(action.payload_schema || {})
      .filter(([name]) => !Object.prototype.hasOwnProperty.call(studySchema || {}, name) && name !== credentialField)
      .map(([name, field]) => renderActionField(name, field))
      .join('');
    return `
      <div class="study-plugin-action" data-study-plugin-action-form="${escapeHtml(action.key)}">
        ${additionalFields}
        <div class="dashboard-actions">
          <button class="btn-secondary${action.danger ? ' plugin-admin-action--danger' : ''}" type="button"
                  data-study-plugin-action="${escapeHtml(action.key)}"
                  data-study-plugin-key="${escapeHtml(plugin.plugin_key)}">
            <i class="iconoir-ev-plug"></i> ${escapeHtml(action.label || humanize(action.key))}
          </button>
        </div>
        <small class="settings-hint" data-study-plugin-action-result="${escapeHtml(action.key)}">${escapeHtml(action.description || '')}</small>
      </div>`;
  }).join('')}</div>`;
}

function renderActionField(name, field) {
  const label = humanize(name);
  const required = field.required === true ? ' required' : '';
  if (field.type === 'boolean') {
    return `<label class="switch-row"><span>${escapeHtml(label)}</span><span class="switch"><input type="checkbox" data-plugin-action-field="${escapeHtml(name)}" data-setting-type="boolean"><span class="switch-slider"></span></span></label>`;
  }
  if (Array.isArray(field.enum) && field.enum.length) {
    return `<label class="field"><span>${escapeHtml(label)}</span><select class="fi-input" data-plugin-action-field="${escapeHtml(name)}" data-setting-type="${escapeHtml(field.type || 'string')}"${required}><option value=""></option>${field.enum.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('')}</select></label>`;
  }
  const inputType = ['integer', 'number'].includes(field.type) ? 'number' : 'text';
  const step = field.type === 'integer' ? ' step="1"' : field.type === 'number' ? ' step="any"' : '';
  const minimum = field.minimum !== undefined ? ` min="${escapeHtml(field.minimum)}"` : '';
  const maximum = field.maximum !== undefined ? ` max="${escapeHtml(field.maximum)}"` : '';
  return `<label class="field"><span>${escapeHtml(label)}</span><input class="fi-input" type="${inputType}" data-plugin-action-field="${escapeHtml(name)}" data-setting-type="${escapeHtml(field.type || 'string')}"${step}${minimum}${maximum}${required}></label>`;
}

async function refreshStudyPluginCredentialStates() {
  const studyId = String(callbacks.getStudyConfig?.().study_id || callbacks.getCurrentStudyName?.() || '').trim();
  if (!studyId) return;
  const controls = byId('study-plugin-destination-options')?.querySelectorAll('[data-study-plugin-credential]') || [];
  if (!controls.length) return;
  try {
    const response = await getJson(`/api/admin/studies/${encodeURIComponent(studyId)}/credentials`);
    controls.forEach((control) => {
      const pluginKey = control.dataset.studyPluginCredential;
      const state = response?.credentials?.[pluginKey] || {};
      const target = control.querySelector('[data-study-plugin-credential-state]');
      if (!target) return;
      target.textContent = state.configured
        ? t('studySettings.credentialConfigured', 'Configured locally ({scope})').replace('{scope}', state.scope || state.source || 'local')
        : t('studySettings.credentialMissing', 'No credential configured');
    });
  } catch (error) {
    console.error('[settings] Could not load study credential state:', error);
  }
}

async function saveStudyPluginCredential(pluginKey) {
  const studyId = String(callbacks.getStudyConfig?.().study_id || callbacks.getCurrentStudyName?.() || '').trim();
  const control = [...(byId('study-plugin-destination-options')?.querySelectorAll('[data-study-plugin-credential]') || [])]
    .find((item) => item.dataset.studyPluginCredential === pluginKey);
  const input = control?.querySelector('[data-study-plugin-credential-input]');
  const value = String(input?.value || '');
  if (!studyId || !value) {
    callbacks.showToast?.(t('studySettings.credentialValueRequired', 'Enter a credential before saving.'), 'error');
    return;
  }
  try {
    await postJson(`/api/admin/studies/${encodeURIComponent(studyId)}/credentials`, { [pluginKey]: value });
    if (input) input.value = '';
    await refreshStudyPluginCredentialStates();
    callbacks.showToast?.(t('studySettings.credentialSaved', 'Credential saved locally.'), 'success');
  } catch (error) {
    callbacks.showToast?.(error.message || t('studySettings.credentialSaveFailed', 'Could not save credential.'), 'error');
  }
}

async function clearStudyPluginCredential(pluginKey) {
  const studyId = String(callbacks.getStudyConfig?.().study_id || callbacks.getCurrentStudyName?.() || '').trim();
  if (!studyId) return;
  if (!window.confirm(t('studySettings.credentialClearConfirm', "Delete this study's local credential?"))) return;
  try {
    await postJson(`/api/admin/studies/${encodeURIComponent(studyId)}/credentials`, { [`clear_${pluginKey}`]: true });
    await refreshStudyPluginCredentialStates();
    callbacks.showToast?.(t('studySettings.credentialCleared', 'Credential deleted.'), 'success');
  } catch (error) {
    callbacks.showToast?.(error.message || t('studySettings.credentialClearFailed', 'Could not delete credential.'), 'error');
  }
}

async function runStudyPluginAction(button) {
  const pluginKey = String(button.dataset.studyPluginKey || '');
  const actionKey = String(button.dataset.studyPluginAction || '');
  const plugin = getPluginCatalog().plugins_by_key[pluginKey];
  const action = (plugin?.capability_config?.admin_actions?.actions || [])
    .find((candidate) => candidate.key === actionKey);
  if (!plugin || !action) return;
  if (action.confirm && !window.confirm(action.description || action.label || actionKey)) return;

  const row = button.closest('[data-study-destination]');
  const actionForm = button.closest('[data-study-plugin-action-form]');
  const credentialField = String(plugin.capability_config?.credentials?.config_field || '');
  const payload = {};
  Object.entries(action.payload_schema || {}).forEach(([name, field]) => {
    let input = [...(row?.querySelectorAll('[data-plugin-setting]') || [])]
      .find((candidate) => candidate.dataset.pluginSetting === name);
    if (!input && name === credentialField) input = row?.querySelector('[data-study-plugin-credential-input]');
    if (!input) {
      input = [...(actionForm?.querySelectorAll('[data-plugin-action-field]') || [])]
        .find((candidate) => candidate.dataset.pluginActionField === name);
    }
    if (!input) return;
    const type = field.type || input.dataset.settingType || 'string';
    const raw = type === 'boolean' ? Boolean(input.checked) : String(input.value || '');
    if (raw === '' && field.required !== true) return;
    payload[name] = type === 'integer'
      ? Number.parseInt(raw, 10)
      : type === 'number'
        ? Number(raw)
        : raw;
  });

  const result = actionForm?.querySelector(`[data-study-plugin-action-result="${actionKey}"]`);
  button.disabled = true;
  try {
    const response = await postJson(
      `/api/admin/plugins/${encodeURIComponent(pluginKey)}/actions/${encodeURIComponent(actionKey)}`,
      payload,
    );
    const details = response?.result || {};
    if (result) result.textContent = details.message || details.last_message || t('studySettings.pluginActionDone', 'Action completed.');
    callbacks.showToast?.(t('studySettings.pluginActionDone', 'Action completed.'), 'success');
  } catch (error) {
    if (result) result.textContent = error.message || t('studySettings.pluginActionFailed', 'Action failed.');
    callbacks.showToast?.(error.message || t('studySettings.pluginActionFailed', 'Action failed.'), 'error');
  } finally {
    button.disabled = false;
  }
}

async function removeUnavailablePlugin(pluginKey) {
  if (!pluginKey) return;
  const message = t('studySettings.removeUnavailableConfirm', 'Remove the saved configuration for {plugin}? This cannot be restored unless you undo the study file change.')
    .replace('{plugin}', pluginKey);
  if (!window.confirm(message)) return;
  const current = normalizeStudySettings(callbacks.getStudyConfig?.().study_settings);
  const plugins = { ...current.plugins };
  const sensors = { ...current.sensors };
  delete plugins[pluginKey];
  delete sensors[pluginKey];
  callbacks.setStudySettings?.({ ...current, plugins, sensors });
  await callbacks.saveStudyConfig?.({ successMessage: t('studySettings.pluginConfigurationRemoved', 'Plugin configuration removed.') });
  renderStudySettingsPanel();
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
  // Preserve legacy sensor selections for unavailable plugins. Their canonical
  // plugin entry is also retained below; neither is removable as a side effect
  // of saving a completely different installed sensor.
  const sensors = { ...current.sensors };
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
