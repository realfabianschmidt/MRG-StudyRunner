/**
 * The machine-level settings shell - everything that belongs to *this computer*.
 *
 * Sensor timeouts, the certificate, the tablet links, the app update, the
 * desktop shortcut and the audit text all live here and never travel with a
 * study. The per-study counterpart is settings/study/study-settings-panel.js,
 * reachable only from the study editor; keeping the two in separate folders is
 * what stops a study copied to another lab from carrying that lab's device
 * setup with it.
 *
 * This module owns the nav and the generated sensor forms. The fixed panels
 * (certificate, update, shortcut, audit) are static markup in admin.html so
 * their existing controllers keep binding to the same element ids - the shell
 * only tells them when they are on screen.
 */
import { t } from '../../shared/i18n.js';
import { byId, escapeHtml } from '../../shared/dom-utils.js';
import { getJson, postJson } from '../../shared/api-client.js';
import { activateShellPanel, bindShellNav, renderShellNav, renderShellPanel } from '../../shared/settings-shell.js';
import { refreshCertificateStatus } from './certificate-settings-controller.js';
import { refreshBrandingSettings, renderBrandingSettingsPanel } from './branding-settings-controller.js';
import {
  PLUGIN_UI_SURFACES,
  getPluginCatalog,
  getPluginCatalogGeneration,
  isPluginVisible,
  loadPluginCatalog,
  pluginUiIcon,
} from '../../shared/plugin-catalog.js';

/**
 * The admin controller's shared state and view plumbing.
 *
 * Passed by reference rather than copied, so a status load here is visible to
 * the dashboard poll there without a second fetch.
 */
let host = {};

/**
 * The catalog generation the visible shell was built from.
 *
 * `getPluginCatalog()` answers with an empty catalog while its fetch is still
 * in flight, so a shell built too early lists no sensors at all. Remembering
 * which generation produced the current markup is what lets a later arrival
 * re-render instead of leaving the operator with an empty page until they
 * close and re-open the settings.
 */
let renderedCatalogGeneration = 0;

export function initializeMachineSettingsPanel(options = {}) {
  host = options;
}

/**
 * Open the machine-level settings shell.
 *
 * Content is unchanged from the modal this replaces - only the shell around it
 * is new. The status load runs while the sweep still covers the screen, so the
 * panels are already filled when the shell appears.
 */
export function openSettingsHub() {
  // Render inside the covered frame, then let the status load fill in
  // afterwards. Awaiting the status fetches under the cover made opening
  // settings visibly slower than every other view for no benefit - but the
  // catalog is awaited, because a shell built without it has no sensor
  // entries at all. That call is cached, so a warm open still costs nothing.
  return host.switchView('view-machine-settings', {
    onCovered: async () => {
      try {
        await loadPluginCatalog();
      } catch (error) {
        console.debug('[admin] Plugin catalog is not available yet:', error);
      }
      renderSettingsHubShell();
    },
  }).then(() => { void loadSettingsHubStatus(); });
}

export function isSettingsHubOpen() {
  return Boolean(byId('view-machine-settings')?.classList.contains('active'));
}

export function renderSettingsHubShell() {
  const nav = byId('machine-settings-nav');
  const panels = byId('machine-settings-panels');
  if (!nav || !panels) return;

  const entries = settingsHubEntries();
  if (!entries.some((entry) => entry.key === host.state.settingsHubActiveTab)) {
    host.state.settingsHubActiveTab = entries[0]?.key || 'tablet';
  }

  nav.innerHTML = renderShellNav(entries, host.state.settingsHubActiveTab);
  panels.innerHTML = settingsHubStatusRow() + settingsHubPanels();
  renderedCatalogGeneration = getPluginCatalogGeneration();

  const root = byId('view-machine-settings');
  host.state.settingsHubActiveTab = activateShellPanel(root, host.state.settingsHubActiveTab);
  bindShellNav(root, (key) => {
    host.state.settingsHubActiveTab = key;
    onSettingsPanelShown(key);
  });
  root?.querySelectorAll('[data-settings-action]').forEach((button) => {
    button.addEventListener('click', () => handleSettingsHubAction(button.dataset.settingsAction || ''));
  });
  root?.querySelectorAll('[data-save-plugin-settings]').forEach((button) => {
    button.addEventListener('click', () => void savePluginSettings(button.dataset.savePluginSettings));
  });
  root?.querySelectorAll('[data-save-plugin-credential]').forEach((button) => {
    button.addEventListener('click', () => void savePluginCredential(button.dataset.savePluginCredential));
  });
  root?.querySelectorAll('[data-clear-plugin-credential]').forEach((button) => {
    button.addEventListener('click', () => void clearPluginCredential(button.dataset.clearPluginCredential));
  });
  root?.querySelector('[data-settings-retry]')?.addEventListener('click', () => void loadSettingsHubStatus());
}

/**
 * Re-render once the catalog arrives after the shell was already built.
 *
 * Called from the admin controller's start-up chain: on a cold start the hub
 * can be open before `/api/plugins/catalog` has answered.
 */
export function refreshSettingsHubIfStale() {
  if (!isSettingsHubOpen()) return;
  if (renderedCatalogGeneration === getPluginCatalogGeneration()) return;
  renderSettingsHubShell();
}

/**
 * One line while the shell is still waiting, and nothing once it is not.
 *
 * Deliberately no "x of n": the settings for every plugin arrive in a single
 * `/api/admin/plugin-settings` response, so there is no per-plugin
 * progression to count. A first attempt counted plugins that had a settings
 * entry against all plugins in the hub and stuck at "5 of 6" forever, because
 * a plugin with no machine settings never appears in that response at all.
 * The remaining *time* is not knowable either - plugin workers are
 * subprocesses whose first call carries a multi-second budget - so this stays
 * an indeterminate line rather than pretending to be a progress bar.
 */
function settingsHubStatusRow() {
  if (host.state.settingsHubError) {
    return `
      <div class="settings-hub-status settings-hub-status--error">
        <span>${escapeHtml(t('settingsHub.loadFailed', 'Some settings could not be loaded.'))}</span>
        <button class="btn-secondary btn-xs" type="button" data-settings-retry>
          <i class="iconoir-refresh"></i> <span>${escapeHtml(t('settingsHub.retry', 'Try again'))}</span>
        </button>
      </div>`;
  }

  const catalogReady = getPluginCatalogGeneration() > 0;
  if (catalogReady && host.state.pluginSettingsLoaded) return '';

  return `
    <div class="settings-hub-status">
      <span class="settings-hub-status-dot" aria-hidden="true"></span>
      <span>${escapeHtml(t('settingsHub.loadingExtensions', 'Loading extensions ...'))}</span>
    </div>`;
}

/**
 * Load a panel's data when it becomes visible.
 * Panels with fixed markup own their own data; the shell only tells them when
 * they are on screen, so nothing is fetched for pages nobody opened.
 */
function onSettingsPanelShown(key) {
  if (key === 'certificate') void refreshCertificateStatus();
  if (key === 'branding') void refreshBrandingSettings();
  if (key === 'update') void host.loadUpdateStatus({ silent: true });
}

function settingsHubEntries() {
  const groupThisComputer = t('settingsHub.groupComputer', 'This computer');
  const groupSensors = t('settingsHub.groupSensors', 'Sensors');
  const groupSystem = t('settingsHub.groupSystem', 'System');
  const entries = [
    { key: 'certificate', icon: 'iconoir-shield-check', label: t('hub.certificateSettings', 'Certificate'), group: groupThisComputer },
    { key: 'tablet', icon: 'iconoir-smartphone-device', label: t('settingsHub.tabTablet', 'Tablet'), group: groupThisComputer },
    { key: 'branding', icon: 'iconoir-media-image', label: t('branding.title', 'Logos'), group: groupThisComputer },
    ...settingsHubPlugins().map((plugin) => ({
      key: `plugin:${plugin.key}`,
      icon: pluginIcon(plugin),
      label: plugin.label || plugin.key,
      group: groupSensors,
    })),
    { key: 'update', icon: 'iconoir-download-circle', label: t('update.title', 'Python app update'), group: groupSystem },
    { key: 'shortcut', icon: 'iconoir-computer', label: t('hub.createShortcut', 'Create desktop shortcut'), group: groupSystem },
    { key: 'audit', icon: 'iconoir-book', label: t('hub.auditSensorSetup', 'Audit & Sensor Setup'), group: groupSystem },
  ];
  if (getPluginCatalog().invalid_plugins.length) {
    entries.push({
      key: 'plugin-problems',
      icon: 'iconoir-warning-triangle',
      label: t('settingsHub.pluginProblems', 'Plugin problems'),
      group: groupSystem,
    });
  }
  return entries;
}

function settingsHubPanels() {
  const active = host.state.settingsHubActiveTab;
  return [
    renderShellPanel('tablet', renderTabletAccessPanel(), active !== 'tablet'),
    renderShellPanel('branding', renderBrandingSettingsPanel(), active !== 'branding'),
    ...(getPluginCatalog().invalid_plugins.length ? [renderShellPanel(
      'plugin-problems',
      renderInvalidPlugins(),
      active !== 'plugin-problems',
    )] : []),
    ...settingsHubPlugins().map((plugin) => renderShellPanel(
      `plugin:${plugin.key}`,
      renderPluginSettingsPanel(plugin),
      active !== `plugin:${plugin.key}`,
    )),
  ].join('');
}

function renderInvalidPlugins() {
  const invalidPlugins = getPluginCatalog().invalid_plugins;
  return `
    <div class="settings-hub-plugin">
      <div class="dashboard-card-title"><i class="iconoir-warning-triangle"></i> <span>${escapeHtml(t('settingsHub.pluginProblems', 'Plugin problems'))}</span></div>
      <p class="settings-hint">${escapeHtml(t('settingsHub.pluginProblemsHint', 'Invalid built-in plugins stay isolated and are not loaded. Fix their manifest or entry point, then restart Study Runner.'))}</p>
      <div class="plugin-problem-list">
        ${invalidPlugins.map((plugin) => `
          <div class="plugin-problem-item">
            <strong>${escapeHtml(plugin.plugin_key || plugin.directory || t('settingsHub.unknownPlugin', 'Unknown plugin'))}</strong>
            <ul>${(plugin.errors || []).map((error) => `<li>${escapeHtml(error)}</li>`).join('')}</ul>
          </div>`).join('')}
      </div>
    </div>`;
}

function settingsHubAction(action, icon, title, hint) {
  return `
    <button class="settings-hub-action" type="button" data-settings-action="${escapeHtml(action)}">
      <i class="${escapeHtml(icon)}"></i>
      <span>
        <strong>${escapeHtml(title)}</strong>
        <small>${escapeHtml(hint)}</small>
      </span>
    </button>`;
}

function handleSettingsHubAction(action) {
  if (action === 'shortcut') {
    void host.createDesktopShortcut('btn-create-shortcut', 'shortcut-result');
  } else if (action === 'dashboard') {
    void host.switchView('view-dashboard');
  }
}

/**
 * Load everything the shell needs, judging each source on its own.
 *
 * The three fetches used to run sequentially inside one `try`, so a slow or
 * failing `/api/admin/status` meant the other two never ran: `pluginSettings`
 * stayed empty, every plugin's settings block rendered as an empty string, and
 * the only way out was to leave the settings and come back once the server had
 * warmed up. They are independent, so they now run together and a failure of
 * one no longer hides the other two.
 */
export async function loadSettingsHubStatus() {
  const [status, settings, hardware] = await Promise.allSettled([
    getJson('/api/admin/status', { timeoutMs: 1500 }),
    getJson('/api/admin/plugin-settings', { timeoutMs: 1500 }),
    getJson('/api/hardware-config', { timeoutMs: 1500 }),
  ]);

  if (status.status === 'fulfilled') {
    host.state.settingsHubStatus = status.value;
    host.state.tabletGate = status.value?.study_clients?.single_tablet || host.state.tabletGate;
    host.state.studyRunState = status.value?.study_run_state || host.state.studyRunState;
  } else {
    console.debug('[admin] Could not load settings hub plugin status:', status.reason);
  }
  if (settings.status === 'fulfilled') {
    host.state.pluginSettings = settings.value.plugins || {};
    host.state.pluginSettingsRevision = settings.value.revision || '';
    host.state.pluginSettingsLoaded = true;
  } else {
    console.debug('[admin] Could not load plugin settings schema:', settings.reason);
  }
  if (hardware.status === 'fulfilled') {
    host.state.hardwareConfig = hardware.value;
  } else {
    console.debug('[admin] Could not load redacted plugin credential state:', hardware.reason);
  }

  // A silent `console.debug` left the operator on a page missing whole blocks
  // with nothing to act on, so the shell says so and offers the retry.
  host.state.settingsHubError = [status, settings, hardware]
    .some((result) => result.status === 'rejected');

  host.renderStudyRunState();
  if (isSettingsHubOpen()) {
    renderSettingsHubShell();
  }
}

function settingsHubPlugins() {
  const plugins = host.state.settingsHubStatus?.plugins || {};
  return getPluginCatalog().plugins
    .filter((manifest) => isPluginVisible(manifest, PLUGIN_UI_SURFACES.SETTINGS_HUB))
    .map((manifest) => {
      const status = plugins[manifest.plugin_key] || {};
      return {
        ...status,
        key: manifest.plugin_key,
        label: status.label || manifest.ui?.label || manifest.plugin_key,
        category: status.category || manifest.category,
        manifest: status.manifest || manifest,
      };
    });
}

function renderPluginSettingsPanel(plugin) {
  // Deliberately no live status here. This page configures the sensor; what it
  // is doing right now belongs on the dashboard, and mixing the two made the
  // same numbers appear in two places with different refresh rates.
  return `
    <div class="settings-hub-plugin">
      <div class="dashboard-card-title"><i class="${escapeHtml(pluginIcon(plugin))}"></i> <span>${escapeHtml(plugin.label || plugin.key)}</span></div>
      ${plugin.manifest?.ui?.description ? `<p class="settings-hint">${escapeHtml(plugin.manifest.ui.description)}</p>` : ''}
      ${renderPluginSettingsForm(plugin.key)}
      ${renderPluginCredentialForm(plugin)}
      <div class="dashboard-actions">
        ${settingsHubAction('dashboard', pluginIcon(plugin), t('settingsHub.openLiveControls', 'Open live controls'), t('settingsHub.openLiveControlsHint', 'Live start, stop, recovery, and monitoring stay on the dashboard.'))}
      </div>
    </div>
  `;
}

function renderTabletAccessPanel() {
  const adminUrl = host.getAccessUrl('admin') || '-';
  const participantUrl = host.getAccessUrl('participant') || '-';
  return `
    <article class="dashboard-card dashboard-card--wide">
      <div class="dashboard-card-title"><i class="iconoir-wifi"></i> <span>${escapeHtml(t('access.browserLinks', 'Browser links'))}</span></div>
      <div class="status-grid status-grid--row">
        <div class="status-card">
          <div class="status-card-label">${escapeHtml(t('access.admin', 'Admin'))}</div>
          <div class="status-card-value">${escapeHtml(adminUrl)}</div>
        </div>
        <div class="status-card">
          <div class="status-card-label">${escapeHtml(t('access.participant', 'Participant'))}</div>
          <div class="status-card-value">${escapeHtml(participantUrl)}</div>
        </div>
      </div>
      <div class="settings-hint">${escapeHtml(t('access.hint', 'Use the participant link from a tablet or browser on the same private network.'))}</div>
    </article>
  `;
}

/**
 * Editable machine settings, generated from the plugin manifest.
 *
 * The backend hands over schema *and* current value together, so the effective
 * value rule (disk wins, manifest default only fills a missing key) lives in
 * one tested place rather than being re-derived here.
 */
function renderPluginSettingsForm(pluginKey) {
  const entry = host.state.pluginSettings?.[pluginKey];
  if (!entry?.fields?.length) {
    // "Not loaded yet" and "this plugin has no machine settings" are
    // indistinguishable from the state alone, so the flag decides whether the
    // operator sees a placeholder or an intentionally absent block.
    if (host.state.pluginSettingsLoaded) return '';
    return `
      <div class="plugin-settings-form">
        <div class="dashboard-card-title"><i class="iconoir-settings"></i> <span>${escapeHtml(t('pluginSettings.title', 'Machine settings'))}</span></div>
        <p class="settings-hint">${escapeHtml(t('pluginSettings.loading', 'Loading settings ...'))}</p>
      </div>`;
  }

  const fields = entry.fields.map((field) => {
    const inputId = `plugin-setting-${pluginKey}-${field.name}`.replace(/[^A-Za-z0-9_-]/g, '-');
    const label = escapeHtml(field.label_key ? t(field.label_key, field.path) : field.path);
    // Bracketed, because "Scan duration s" reads as a typo while "Scan duration (s)"
    // reads as a unit.
    const unit = field.unit ? ` <span class="settings-unit">(${escapeHtml(field.unit)})</span>` : '';
    return `<div class="field" data-setting-field="${escapeHtml(field.name)}">${
      field.type === 'boolean'
        ? `<label class="switch-row" for="${inputId}"><span>${label}</span>
             <span class="switch"><input type="checkbox" id="${inputId}" data-setting-name="${escapeHtml(field.name)}"${field.value ? ' checked' : ''}><span class="switch-slider"></span></span>
           </label>`
        : `<label for="${inputId}">${label}${unit}</label>${renderSettingInput(inputId, field)}`
    }</div>`;
  }).join('');

  return `
    <div class="plugin-settings-form" data-plugin-settings="${escapeHtml(pluginKey)}">
      <div class="dashboard-card-title"><i class="iconoir-settings"></i> <span>${escapeHtml(t('pluginSettings.title', 'Machine settings'))}</span></div>
      <p class="settings-hint">${escapeHtml(t('pluginSettings.hint', 'These belong to this computer. Which sensors a study uses is set in the study editor.'))}</p>
      ${fields}
      <div class="dashboard-actions">
        <button class="btn-primary" type="button" data-save-plugin-settings="${escapeHtml(pluginKey)}">
          <i class="iconoir-floppy-disk"></i> <span>${escapeHtml(t('pluginSettings.save', 'Save settings'))}</span>
        </button>
      </div>
      <div class="settings-hint" data-settings-feedback="${escapeHtml(pluginKey)}"></div>
    </div>`;
}

function renderSettingInput(inputId, field) {
  const name = escapeHtml(field.name);
  if (field.type === 'choice') {
    const options = (field.options || []).map((option) =>
      `<option value="${escapeHtml(option)}"${option === field.value ? ' selected' : ''}>${escapeHtml(option)}</option>`).join('');
    return `<select id="${inputId}" data-setting-name="${name}">${options}</select>`;
  }
  if (field.type === 'number') {
    const min = field.minimum !== null && field.minimum !== undefined ? ` min="${escapeHtml(String(field.minimum))}"` : '';
    const max = field.maximum !== null && field.maximum !== undefined ? ` max="${escapeHtml(String(field.maximum))}"` : '';
    return `<input type="number" step="any" id="${inputId}" data-setting-name="${name}" value="${escapeHtml(String(field.value ?? ''))}"${min}${max}>`;
  }
  return `<input type="text" id="${inputId}" data-setting-name="${name}" value="${escapeHtml(String(field.value ?? ''))}">`;
}

async function savePluginSettings(pluginKey) {
  const form = document.querySelector(`[data-plugin-settings="${pluginKey}"]`);
  const feedback = document.querySelector(`[data-settings-feedback="${pluginKey}"]`);
  if (!form) return;

  const settings = {};
  form.querySelectorAll('[data-setting-name]').forEach((input) => {
    settings[input.dataset.settingName] = input.type === 'checkbox' ? input.checked : input.value;
  });

  try {
    const response = await postJson(`/api/admin/plugin-settings/${encodeURIComponent(pluginKey)}`, {
      settings,
      revision: host.state.pluginSettingsRevision || undefined,
    });
    host.state.pluginSettings = response.plugins || host.state.pluginSettings;
    host.state.pluginSettingsRevision = response.revision || host.state.pluginSettingsRevision;
    if (feedback) {
      feedback.textContent = response.restart_required
        ? t('pluginSettings.savedRestart', 'Saved. Restart Study Runner for this to take effect.')
        : t('pluginSettings.saved', 'Saved.');
    }
    host.showToast(t('pluginSettings.saved', 'Saved.'), 'success');
  } catch (error) {
    console.error('[settings] Could not save plugin settings:', error);
    if (feedback) feedback.textContent = error.message || t('pluginSettings.saveFailed', 'Could not save.');
    host.showToast(t('pluginSettings.saveFailed', 'Could not save.'), 'error');
  }
}

function renderPluginCredentialForm(plugin) {
  const manifest = plugin.manifest || {};
  const credential = manifest.capability_config?.credentials || {};
  const field = String(credential.config_field || '');
  if (!field) return '';
  const section = host.state.hardwareConfig?.[plugin.key] || {};
  const configured = section[`${field}_configured`] === true;
  const scope = String(section[`${field}_scope`] || section[`${field}_source`] || '');
  const status = configured
    ? t('pluginSettings.credentialConfigured', 'Configured locally ({scope})').replace('{scope}', scope || 'local')
    : t('pluginSettings.credentialMissing', 'No credential configured');
  return `
    <div class="plugin-settings-form" data-plugin-credential="${escapeHtml(plugin.key)}">
      <div class="dashboard-card-title"><i class="iconoir-key"></i> <span>${escapeHtml(t('pluginSettings.credentialTitle', 'Local credential'))}</span></div>
      <p class="settings-hint">${escapeHtml(t('pluginSettings.credentialHint', 'The saved value remains in the local secret store and is never returned to this page.'))}</p>
      <label class="field">
        <span>${escapeHtml(humanize(field))}</span>
        <input type="password" autocomplete="new-password" data-plugin-credential-input>
        <small class="settings-hint" data-plugin-credential-state>${escapeHtml(status)}</small>
      </label>
      <div class="dashboard-actions">
        <button class="btn-primary" type="button" data-save-plugin-credential="${escapeHtml(plugin.key)}">
          <i class="iconoir-floppy-disk"></i> ${escapeHtml(t('pluginSettings.saveCredential', 'Save credential'))}
        </button>
        <button class="btn-secondary" type="button" data-clear-plugin-credential="${escapeHtml(plugin.key)}">
          <i class="iconoir-trash"></i> ${escapeHtml(t('pluginSettings.clearCredential', 'Delete credential'))}
        </button>
      </div>
    </div>`;
}

async function savePluginCredential(pluginKey) {
  const form = [...(document.querySelectorAll('[data-plugin-credential]') || [])]
    .find((candidate) => candidate.dataset.pluginCredential === pluginKey);
  const input = form?.querySelector('[data-plugin-credential-input]');
  const manifest = getPluginCatalog().plugins_by_key[pluginKey];
  const field = String(manifest?.capability_config?.credentials?.config_field || '');
  const value = String(input?.value || '');
  if (!field || !value) {
    host.showToast?.(t('pluginSettings.credentialValueRequired', 'Enter a credential before saving.'), 'error');
    return;
  }
  try {
    await postJson('/api/hardware-config', {
      _revision: host.state.hardwareConfig?._revision || undefined,
      [pluginKey]: { [field]: value },
    });
    if (input) input.value = '';
    host.showToast?.(t('pluginSettings.credentialSaved', 'Credential saved locally.'), 'success');
    await loadSettingsHubStatus();
  } catch (error) {
    host.showToast?.(error.message || t('pluginSettings.credentialSaveFailed', 'Could not save credential.'), 'error');
  }
}

async function clearPluginCredential(pluginKey) {
  const manifest = getPluginCatalog().plugins_by_key[pluginKey];
  const field = String(manifest?.capability_config?.credentials?.config_field || '');
  if (!field) return;
  if (!window.confirm(t('pluginSettings.credentialClearConfirm', 'Delete the locally stored credential?'))) return;
  try {
    await postJson('/api/hardware-config', {
      _revision: host.state.hardwareConfig?._revision || undefined,
      [pluginKey]: { [`clear_${field}`]: true },
    });
    host.showToast?.(t('pluginSettings.credentialCleared', 'Credential deleted.'), 'success');
    await loadSettingsHubStatus();
  } catch (error) {
    host.showToast?.(error.message || t('pluginSettings.credentialClearFailed', 'Could not delete credential.'), 'error');
  }
}

function humanize(value) {
  return String(value || '').replace(/[._-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function pluginIcon(plugin) {
  return pluginUiIcon(plugin.manifest || plugin);
}
