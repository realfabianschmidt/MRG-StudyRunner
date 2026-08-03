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
import { t } from '../../i18n.js';
import { byId, escapeHtml } from '../../lib/dom-utils.js';
import { getJson, postJson } from '../../api-client.js';
import { activateShellPanel, bindShellNav, renderShellNav, renderShellPanel } from '../../lib/settings-shell.js';
import { refreshCertificateStatus } from './certificate-settings-controller.js';
import {
  PLUGIN_UI_SURFACES,
  getPluginCatalog,
  isPluginVisible,
  pluginUiIcon,
} from '../../lib/plugin-catalog.js';

/**
 * The admin controller's shared state and view plumbing.
 *
 * Passed by reference rather than copied, so a status load here is visible to
 * the dashboard poll there without a second fetch.
 */
let host = {};

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
  // Render synchronously inside the covered frame, then let the status load
  // fill in afterwards. Awaiting two fetches under the cover made opening
  // settings visibly slower than every other view for no benefit.
  return host.switchView('view-machine-settings', { onCovered: renderSettingsHubShell })
    .then(() => { void loadSettingsHubStatus(); });
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
  panels.innerHTML = settingsHubPanels();

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
}

/**
 * Load a panel's data when it becomes visible.
 * Panels with fixed markup own their own data; the shell only tells them when
 * they are on screen, so nothing is fetched for pages nobody opened.
 */
function onSettingsPanelShown(key) {
  if (key === 'certificate') void refreshCertificateStatus();
  if (key === 'update') void host.loadUpdateStatus({ silent: true });
}

function settingsHubEntries() {
  const groupThisComputer = t('settingsHub.groupComputer', 'This computer');
  const groupSensors = t('settingsHub.groupSensors', 'Sensors');
  const groupSystem = t('settingsHub.groupSystem', 'System');
  const entries = [
    { key: 'certificate', icon: 'iconoir-shield-check', label: t('hub.certificateSettings', 'Certificate'), group: groupThisComputer },
    { key: 'tablet', icon: 'iconoir-tablet', label: t('settingsHub.tabTablet', 'Tablet'), group: groupThisComputer },
    ...settingsHubPlugins().map((plugin) => ({
      key: `plugin:${plugin.key}`,
      icon: pluginIcon(plugin),
      label: plugin.label || plugin.key,
      group: groupSensors,
    })),
    { key: 'update', icon: 'iconoir-download-circled-outline', label: t('update.title', 'Python app update'), group: groupSystem },
    { key: 'shortcut', icon: 'iconoir-desktop', label: t('hub.createShortcut', 'Create desktop shortcut'), group: groupSystem },
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

export async function loadSettingsHubStatus() {
  try {
    host.state.settingsHubStatus = await getJson('/api/admin/status', { timeoutMs: 1500 });
    try {
      host.state.pluginSettings = (await getJson('/api/admin/plugin-settings', { timeoutMs: 1500 })).plugins || {};
    } catch (settingsError) {
      console.debug('[admin] Could not load plugin settings schema:', settingsError);
    }
    host.state.tabletGate = host.state.settingsHubStatus?.study_clients?.single_tablet || host.state.tabletGate;
    host.state.studyRunState = host.state.settingsHubStatus?.study_run_state || host.state.studyRunState;
    host.renderStudyRunState();
    if (isSettingsHubOpen()) {
      renderSettingsHubShell();
    }
  } catch (error) {
    console.debug('[admin] Could not load settings hub plugin status:', error);
  }
}

function settingsHubPlugins() {
  const integrations = host.state.settingsHubStatus?.integrations || {};
  return getPluginCatalog().plugins
    .filter((manifest) => isPluginVisible(manifest, PLUGIN_UI_SURFACES.SETTINGS_HUB))
    .map((manifest) => {
      const status = integrations[manifest.plugin_key] || {};
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
      ${renderPluginSettingsForm(plugin.key)}
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
  if (!entry?.fields?.length) return '';

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
    return `<select class="fi-input" id="${inputId}" data-setting-name="${name}">${options}</select>`;
  }
  if (field.type === 'number') {
    const min = field.minimum !== null && field.minimum !== undefined ? ` min="${escapeHtml(String(field.minimum))}"` : '';
    const max = field.maximum !== null && field.maximum !== undefined ? ` max="${escapeHtml(String(field.maximum))}"` : '';
    return `<input class="fi-input" type="number" step="any" id="${inputId}" data-setting-name="${name}" value="${escapeHtml(String(field.value ?? ''))}"${min}${max}>`;
  }
  return `<input class="fi-input" type="text" id="${inputId}" data-setting-name="${name}" value="${escapeHtml(String(field.value ?? ''))}">`;
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
    const response = await postJson(`/api/admin/plugin-settings/${encodeURIComponent(pluginKey)}`, { settings });
    host.state.pluginSettings = response.plugins || host.state.pluginSettings;
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

function pluginIcon(plugin) {
  return pluginUiIcon(plugin.manifest || plugin);
}
