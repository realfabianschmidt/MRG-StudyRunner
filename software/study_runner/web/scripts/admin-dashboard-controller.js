import { getJson, postJson } from './api-client.js';

import { t } from './i18n.js';
import { escapeHtml } from './lib/dom-utils.js';
import {
  getPluginCatalog,
  getPluginUiExtension,
  isPluginVisible,
  loadPluginCatalog,
  loadPluginUiExtensions,
  PLUGIN_UI_SURFACES,
  pluginByKey,
  pluginUiIcon,
} from './lib/plugin-catalog.js';

const POLL_INTERVAL_MS = 2000;

let pollTimer = null;
let callbacks = {};

export function initializeAdminDashboard(options = {}) {
  callbacks = options;
  const { showToast } = options;
  const elements = getDashboardElements();
  if (!elements.dashboardButton || !elements.dashboard) {
    return;
  }

  elements.dashboardButton.addEventListener('click', () => showDashboard(elements));
  elements.backButton?.addEventListener('click', () => showEditor(elements));
  elements.dashboard.addEventListener('click', (event) => {
    const button = event.target.closest('[data-dashboard-action]');
    if (button) {
      void runDashboardAction(button, elements, showToast);
    }
  });

  const refresh = () => {
    void refreshAdminStatus(elements, showToast);
  };

  refresh();
  pollTimer = window.setInterval(refresh, POLL_INTERVAL_MS);
}

async function runDashboardAction(actionSource, elements, showToast) {
  const button = typeof actionSource === 'string' ? null : actionSource;
  const action = typeof actionSource === 'string' ? actionSource : button?.dataset.dashboardAction || '';
  if (button?.dataset.pluginAdminAction && button.dataset.pluginKey) {
    await runPluginAdminAction(button, elements, showToast);
    return;
  }
  if (action === 'reset_sensor_overrides') {
    await resetSensorOverrides(elements, showToast);
    return;
  }
  if (action === 'open_settings') {
    callbacks.openSettingsHub?.();
    return;
  }
  const toggleMatch = action.match(/^toggle_(.+)_(on|off)$/);
  if (toggleMatch) {
    const [, pluginKey, state] = toggleMatch;
    await updatePluginToggle(pluginKey, state === 'on', elements, showToast);
    return;
  }

  const runtimeMatch = action.match(/^runtime_(.+)_(start|stop|restart)$/);
  if (!runtimeMatch) {
    return;
  }

  const [, pluginKey, runtimeAction] = runtimeMatch;
  try {
    const response = await postJson(`/api/admin/plugins/${encodeURIComponent(pluginKey)}/${runtimeAction}`, {});
    if (response?.study_controlled) {
      showToast?.(t('dashboard.studyControlledWarning', 'Temporary dashboard overrides can overrule the study settings during this server session.'), 'warning');
      await refreshAdminStatus(elements, showToast);
      return;
    }
    showToast?.(t('dashboard.actionSent', 'Dashboard action sent'), 'success');
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Dashboard action failed:', error);
    showToast?.(t('dashboard.actionFailed', 'Dashboard action failed'), 'error');
  }
}

async function runPluginAdminAction(button, elements, showToast) {
  const pluginKey = button.dataset.pluginKey || '';
  const actionKey = button.dataset.pluginAdminAction || '';
  const declared = (pluginByKey(pluginKey)?.capability_config?.admin_actions?.actions || [])
    .find((candidate) => candidate.key === actionKey);
  if (!declared) return;
  if (declared.confirm && !window.confirm(declared.description || declared.label || actionKey)) return;

  button.disabled = true;
  try {
    let payload = {};
    if (button.dataset.pluginAdminPayload) {
      try {
        payload = JSON.parse(button.dataset.pluginAdminPayload);
      } catch {
        throw new Error(t('dashboard.pluginActionPayloadInvalid', 'Plugin action payload is invalid'));
      }
    }
    const response = await postJson(
      `/api/admin/plugins/${encodeURIComponent(pluginKey)}/actions/${encodeURIComponent(actionKey)}`,
      payload,
    );
    const result = response?.result || {};
    showToast?.(
      result.last_message || result.message || t('dashboard.pluginActionDone', 'Plugin action completed'),
      'success',
    );
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Plugin action failed:', error);
    showToast?.(error.message || t('dashboard.pluginActionFailed', 'Plugin action failed'), 'error');
  } finally {
    button.disabled = false;
  }
}

async function resetSensorOverrides(elements, showToast) {
  try {
    await postJson('/api/admin/session-overrides/reset', {});
    showToast?.(t('dashboard.overrideResetDone', 'Temporary dashboard overrides reset'), 'success');
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Sensor override reset failed:', error);
    showToast?.(t('dashboard.overrideResetFailed', 'Could not reset dashboard overrides'), 'error');
  }
}

async function updatePluginToggle(pluginKey, enabled, elements, showToast) {
  try {
    const response = await postJson(`/api/admin/plugins/${encodeURIComponent(pluginKey)}/enabled`, { enabled });
    const messageKey = enabled ? 'dashboard.pluginEnabled' : 'dashboard.pluginDisabled';
    const fallback = enabled ? '{name} enabled' : '{name} disabled';
    showToast?.(t(messageKey, fallback).replace('{name}', formatPluginName(pluginKey)), 'success');
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Plugin toggle failed:', error);
    showToast?.(t('dashboard.pluginToggleFailed', 'Plugin toggle failed'), 'error');
  }
}

function getDashboardElements() {
  return {
    editView: document.getElementById('admin-edit-view'),
    dashboard: document.getElementById('admin-dashboard'),
    dashboardButton: document.getElementById('btn-admin-dashboard'),
    backButton: document.getElementById('btn-admin-edit-view'),
    clients: document.getElementById('dashboard-clients'),
    sensorTiles: document.getElementById('dashboard-sensor-tiles'),
    controls: document.getElementById('dashboard-plugin-controls'),
    xdf: document.getElementById('dashboard-xdf'),
  };
}

async function refreshAdminStatus(elements, showToast) {
  try {
    const [status, runtimeInfo] = await Promise.all([
      getJson('/api/admin/status'),
      getJson('/api/runtime-info'),
      loadPluginCatalog(),
    ]);
    await loadPluginUiExtensions('dashboard');
    status.runtime_info = runtimeInfo;
    renderAdminStatus(elements, status);
  } catch (error) {
    console.error('[admin] Could not load admin status:', error);
    showToast?.(t('dashboard.statusFailed', 'Dashboard status failed'), 'error');
  }
}

function renderAdminStatus(elements, status) {
  const clients = status.study_clients || {};
  elements.dashboardButton.hidden = false;

  renderClients(elements.clients, clients.clients || []);
  renderSensorTiles(elements.sensorTiles, status);
  renderPluginControls(elements.controls, status.plugins || {}, status);
  renderXdf(elements.xdf, status);
  applyRunScope(status);
}

function renderSensorTiles(target, status) {
  if (!target) return;
  const plugins = status.plugins || {};
  const items = getPluginCatalog().plugins
    .filter((manifest) => isPluginVisible(manifest, PLUGIN_UI_SURFACES.DASHBOARD))
    .map((manifest) => ({
      ...(plugins[manifest.plugin_key] || {}),
      key: manifest.plugin_key,
      label: plugins[manifest.plugin_key]?.label || manifest.ui?.label || manifest.plugin_key,
      manifest,
    }));
  if (!items.length) {
    target.innerHTML = `<p>${escapeHtml(t('dashboard.noPlugins', 'No plugins registered.'))}</p>`;
    return;
  }

  target.innerHTML = items.map((item) => {
    const detail = renderPluginDashboardDetail(item, status);
    const icon = pluginUiIcon(item.manifest);
    return `
      <article class="dashboard-card" data-plugin-tile="${escapeHtml(item.key)}">
        <div class="dashboard-card-title"><i class="${escapeHtml(icon)}"></i> <span>${escapeHtml(item.label || item.key)}</span></div>
        <div class="dashboard-card-body">${detail}${renderPluginAdminActions(item.manifest, item)}</div>
      </article>`;
  }).join('');
}

function renderPluginDashboardDetail(item, status) {
  const extension = getPluginUiExtension(item.manifest, 'dashboard');
  if (!extension) return genericSensorDetail(item);
  try {
    const rendered = extension.renderDashboard(
      { plugin: item, status, manifest: item.manifest },
      dashboardUiHelpers(),
    );
    return typeof rendered === 'string' ? rendered : genericSensorDetail(item);
  } catch (error) {
    console.warn(`[admin] ${item.key} dashboard extension failed:`, error);
    return genericSensorDetail(item);
  }
}

function dashboardUiHelpers() {
  return {
    escapeHtml,
    t,
    statusLabel,
    fieldLabel,
    formatEnabled,
    formatValue,
    formatBoolean,
    formatHealthValue,
    formatSensorChannels,
    formatTimestampAge,
    formatObjectBrief,
    renderRuntimeButtons,
  };
}

function renderPluginAdminActions(manifest, pluginStatus) {
  const actions = manifest?.capability_config?.admin_actions?.actions || [];
  if (!actions.length) return '';
  const buttons = actions.flatMap((action) => renderManifestActionInstances(action, manifest, pluginStatus));
  if (!buttons.length) return '';
  return `
    <div class="dashboard-actions plugin-admin-actions">
      ${buttons.join('')}
    </div>`;
}

function renderManifestActionInstances(action, manifest, pluginStatus) {
  const instanceConfig = action?.instances;
  if (!instanceConfig) {
    return [renderManifestActionButton(action, manifest, {}, '')];
  }
  const instances = (instanceConfig.status_paths || [])
    .map((path) => readObjectPath(pluginStatus, path))
    .find((value) => Array.isArray(value)) || [];
  return instances
    .filter((instance) => instance && typeof instance === 'object' && !Array.isArray(instance))
    .map((instance) => {
      const payload = {};
      Object.entries(instanceConfig.payload_map || {}).forEach(([target, source]) => {
        const value = readObjectPath(instance, source);
        if (value !== undefined && value !== null && value !== '') payload[target] = value;
      });
      const detail = (instanceConfig.label_fields || [])
        .map((path) => readObjectPath(instance, path))
        .filter((value, index, values) => value !== undefined && value !== null && value !== '' && values.indexOf(value) === index)
        .join(' - ');
      return renderManifestActionButton(action, manifest, payload, detail);
    });
}

function renderManifestActionButton(action, manifest, payload, detail) {
  const actionLabel = action.label || formatPluginName(action.key);
  const label = detail ? `${actionLabel}: ${detail}` : actionLabel;
  return `
    <button type="button" class="btn-secondary${action.danger ? ' plugin-admin-action--danger' : ''}"
            data-dashboard-action="plugin_admin_action"
            data-plugin-key="${escapeHtml(manifest.plugin_key)}"
            data-plugin-admin-action="${escapeHtml(action.key)}"
            data-plugin-admin-payload="${escapeHtml(JSON.stringify(payload))}"
            title="${escapeHtml(action.description || actionLabel)}">
      ${escapeHtml(label)}
    </button>`;
}

function readObjectPath(source, path) {
  return String(path || '').split('.').reduce(
    (value, key) => (value && typeof value === 'object' ? value[key] : undefined),
    source,
  );
}

/** Fallback tile for any plugin without a bespoke view. */
function genericSensorDetail(item) {
  return `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(item.status || 'unknown')}">${escapeHtml(statusLabel(item.status))}</span>
      <strong>${formatEnabled(item.configured_enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${fieldLabel('device', 'Device')}</dt><dd>${escapeHtml(item.device_label || '-')}</dd>
      <dt>${fieldLabel('lastActive', 'Last active')}</dt><dd>${formatTimestampAge(item.last_activity_at, item.seconds_since_last_activity)}</dd>
      <dt>${fieldLabel('message', 'Message')}</dt><dd>${escapeHtml(item.last_message || '-')}</dd>
    </dl>
    ${renderRuntimeButtons(item)}`;
}

/**
 * Grey out what only means something during a run.
 *
 * The sensors above are useful any time - that is the point of reaching the
 * dashboard before pressing Play. The live study readout is not.
 */
function applyRunScope(status) {
  const running = (status.study_run_state || {}).status === 'running';
  document.querySelectorAll('[data-run-scoped]').forEach((element) => {
    element.classList.toggle('is-idle', !running);
  });
}

function renderClients(target, clients) {
  if (!target) return;
  if (!clients.length) {
    target.innerHTML = `<p>${escapeHtml(t('dashboard.noClient', 'No connected study client yet.'))}</p>`;
    return;
  }

  target.innerHTML = clients.map((client) => `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(client.status)}">${escapeHtml(statusLabel(client.status))}</span>
      <strong>${escapeHtml(client.participant_id || t('dashboard.noParticipantId', 'No participant ID yet'))}</strong>
    </div>
    <dl class="status-list">
      <dt>${fieldLabel('study', 'Study')}</dt><dd>${escapeHtml(client.study_id || '-')}</dd>
      <dt>${fieldLabel('card', 'Card')}</dt><dd>${formatCard(client)}</dd>
      <dt>${fieldLabel('age', 'Age')}</dt><dd>${escapeHtml(client.age_seconds)}s</dd>
      <dt>${fieldLabel('plugins', 'Plugins')}</dt><dd>${formatClientPluginStatus(client.plugin_status)}</dd>
    </dl>
  `).join('');
}

function renderPluginControls(target, plugins, status = {}) {
  if (!target) return;
  const rows = getPluginCatalog().plugins
    .filter((manifest) => isPluginVisible(manifest, PLUGIN_UI_SURFACES.DASHBOARD))
    .map((manifest) => ({
      ...(plugins[manifest.plugin_key] || {}),
      key: manifest.plugin_key,
      label: plugins[manifest.plugin_key]?.label || manifest.ui?.label || manifest.plugin_key,
      manifest,
    }));
  if (!rows.length) {
    target.innerHTML = `<p>${escapeHtml(t('dashboard.noPlugins', 'No plugins registered.'))}</p>`;
    return;
  }
  const sensorRuntime = status.sensor_runtime || {};
  const hasOverrides = Object.values(sensorRuntime.override_active || {}).some(Boolean)
    || Object.keys(status.session_sensor_overrides || {}).length > 0;
  const warning = hasOverrides
    ? `<div class="plugin-control-warning">${escapeHtml(t('dashboard.overrideWarning', 'Temporary dashboard overrides are active for this server session.'))}</div>`
    : '';
  const resetButton = hasOverrides
    ? `<button type="button" class="btn-secondary btn-xs" data-dashboard-action="reset_sensor_overrides">${escapeHtml(t('dashboard.overrideReset', 'Reset to study settings'))}</button>`
    : '';
  const settingsButton = `<button type="button" class="btn-secondary btn-xs" data-dashboard-action="open_settings"><i class="iconoir-settings"></i> ${escapeHtml(t('dashboard.openSettings', 'Open settings'))}</button>`;
  target.innerHTML = `<div class="plugin-controls">
    <div class="plugin-control-warning plugin-control-warning--info">${escapeHtml(t('dashboard.settingsHint', 'Plugin setup lives in Settings. This dashboard focuses on live status, start, stop, and recovery.'))}</div>
    ${warning}
    <div class="plugin-control-reset">${settingsButton}${resetButton}</div>
    ${rows.map((row) => renderPluginControlRow(row, sensorRuntime)).join('')}
  </div>`;
}

function renderPluginControlRow(item, sensorRuntime = {}) {
  const sensorEffective = sensorRuntime.effective || {};
  const hasSensorState = Object.prototype.hasOwnProperty.call(sensorEffective, item.key);
  const configured = hasSensorState ? Boolean(sensorEffective[item.key]) : Boolean(item.configured_enabled ?? item.enabled);
  const status = item.status || (configured ? 'enabled' : 'disabled');
  const runtimeDetail = hasSensorState ? renderSensorRuntimeDetail(item.key, sensorRuntime) : '';

  return `
    <div class="plugin-control-row">
      <div class="plugin-control-main">
        <span class="status-pill status-pill--${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>
        <div>
          <strong>${escapeHtml(item.label || item.key)}</strong>
          <span>${buildPluginDetail(item)}</span>
          ${runtimeDetail}
        </div>
      </div>
      <div class="plugin-control-actions">
        <span class="plugin-control-note">${escapeHtml(item.can_toggle ? t('dashboard.settingsManagedInHub', 'Settings hub') : t('dashboard.managedByParent', 'Managed by parent plugin'))}</span>
      </div>
    </div>`;
}

function renderRuntimeButtons(item, compact = false) {
  const buttons = [];
  if (item.can_start) buttons.push(['start', t('dashboard.action.start', 'Start')]);
  if (item.can_restart) buttons.push(['restart', t('dashboard.action.restart', 'Restart')]);
  if (item.can_stop) buttons.push(['stop', t('dashboard.action.stop', 'Stop')]);
  if (!buttons.length) return compact ? '' : '<div class="dashboard-actions"></div>';
  const className = compact ? 'btn-secondary btn-xs' : 'btn-secondary';
  const html = buttons.map(([action, label]) => `
    <button type="button" class="${className}" data-dashboard-action="runtime_${escapeHtml(item.key)}_${action}">${escapeHtml(label)}</button>
  `).join('');
  return compact ? html : `<div class="dashboard-actions">${html}</div>`;
}

function buildPluginDetail(item) {
  const details = [];
  if (item.category) details.push(item.category);
  if (item.device_label) details.push(item.device_label);
  if (item.lsl_enabled !== undefined) details.push(`${t('dashboard.dataLsl', 'data LSL')} ${formatOnOff(item.lsl_enabled)}`);
  if (item.recording_enabled !== undefined) details.push(`${t('dashboard.recording', 'recording')} ${formatOnOff(item.recording_enabled)}`);
  if (item.scan_timeout_seconds !== undefined && item.scan_timeout_seconds !== null) details.push(`${t('dashboard.scan', 'scan')} ${item.scan_timeout_seconds}s`);
  if (item.url) details.push(item.url);
  if (item.host) details.push(`${item.host}:${item.port || ''}`);
  return escapeHtml(details.filter(Boolean).join(' - ') || item.last_message || '-');
}

function renderSensorRuntimeDetail(sensorKey, sensorRuntime = {}) {
  const study = sensorRuntime.study || {};
  const overrides = sensorRuntime.overrides || {};
  const overrideActive = sensorRuntime.override_active || {};
  const effective = sensorRuntime.effective || {};
  const hasOverride = Boolean(overrideActive[sensorKey]);
  const overrideLabel = hasOverride ? formatOnOff(overrides[sensorKey]) : t('dashboard.none', 'none');
  return `<span class="plugin-runtime-detail">
    ${escapeHtml(t('dashboard.runtimeStudy', 'Study'))}: ${escapeHtml(formatOnOff(study[sensorKey]))}
    · ${escapeHtml(t('dashboard.runtimeOverride', 'Override'))}: ${escapeHtml(overrideLabel)}
    · ${escapeHtml(t('dashboard.runtimeEffective', 'Effective'))}: ${escapeHtml(formatOnOff(effective[sensorKey]))}
    ${hasOverride ? `<em>${escapeHtml(t('dashboard.temporaryOverride', 'temporary dashboard override'))}</em>` : ''}
  </span>`;
}

function renderXdf(target, status) {
  if (!target) return;
  const infrastructure = status.recording_infrastructure || {};
  const worker = status.recording_worker || {};
  const health = worker.health || {};
  const workerStatus = health.status || worker.status || (infrastructure.available ? 'idle' : 'unavailable');
  const healthAge = Number.isFinite(Number(worker.health_at_epoch))
    ? `${Math.max(0, (Date.now() / 1000) - Number(worker.health_at_epoch)).toFixed(1)} s`
    : '-';
  const workerIssues = Array.isArray(health.issues) ? health.issues : [];
  const issueList = workerIssues.length
    ? `<ul class="status-warning recording-worker-issues">${workerIssues.map((issue) => {
      const code = issue?.code || 'recording_worker_issue';
      const message = issue?.message || issue?.error || code;
      return `<li><strong>${escapeHtml(code)}</strong>: ${escapeHtml(message)}</li>`;
    }).join('')}</ul>`
    : '';
  const state = infrastructure.available
    ? t('dashboard.enabled', 'Enabled')
    : t('dashboard.disabled', 'Disabled');
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(workerStatus)}">${escapeHtml(statusLabel(workerStatus))}</span>
      <strong>${escapeHtml(worker.session_id || t('dashboard.noActiveSession', 'No active recording session'))}</strong>
    </div>
    <dl class="status-list">
      <dt>${fieldLabel('primarySync', 'Primary sync')}</dt><dd>${escapeHtml(status.timestamp_strategy?.primary || 'LSL')}</dd>
      <dt>${fieldLabel('format', 'Format')}</dt><dd>${escapeHtml(status.timestamp_strategy?.recording_format || '.xdf')}</dd>
      <dt>${fieldLabel('recordingWorker', 'Recording worker')}</dt><dd>${escapeHtml(state)}</dd>
      <dt>${fieldLabel('workerHealthAge', 'Worker health age')}</dt><dd>${escapeHtml(healthAge)}</dd>
      <dt>${fieldLabel('workerHealthFailures', 'Health poll failures')}</dt><dd>${escapeHtml(worker.worker_health_failures ?? 0)}</dd>
      <dt>${fieldLabel('canonicalXdf', 'Canonical XDF')}</dt><dd>${formatBoolean(infrastructure.canonical_xdf)}</dd>
      <dt>${fieldLabel('merge', 'Lossless merge')}</dt><dd>${formatBoolean(infrastructure.supports_merge)}</dd>
      <dt>${fieldLabel('message', 'Message')}</dt><dd>${escapeHtml(worker.last_error || infrastructure.reason || '-')}</dd>
    </dl>
    ${issueList}
  `;
}

function showDashboard(elements) {
  elements.editView.hidden = true;
  elements.dashboard.hidden = false;
}

function showEditor(elements) {
  elements.dashboard.hidden = true;
  elements.editView.hidden = false;
}

function statusLabel(status) {
  // Operators see a plain-language label; the raw status stays in the
  // CSS class for styling.
  const raw = String(status || 'unknown');
  return t(`dashboard.status.${raw}`, raw.replace(/_/g, ' '));
}

function fieldLabel(fieldKey, fallback, helpKey = fieldKey) {
  const label = escapeHtml(t(`dashboard.field.${fieldKey}`, fallback));
  const text = t(`dashboard.help.${helpKey}`, '');
  if (!text) return label;
  const safeText = escapeHtml(text);
  const safeLabel = escapeHtml(t('dashboard.helpIconLabel', `{field}`).replace('{field}', fallback));
  return `<span class="status-label-help" tabindex="0" title="${safeText}" aria-label="${safeLabel}: ${safeText}">${label}</span>`;
}

function formatHealthValue(value) {
  if (value === null || value === undefined || value === '') return '-';
  const normalized = String(value).trim().toLowerCase();
  const labels = {
    connected: t('dashboard.health.connected', 'connected'),
    waiting: t('dashboard.health.waiting', 'waiting'),
    unknown: t('dashboard.health.unknown', 'unknown'),
    usable: t('dashboard.health.usable', 'usable'),
    mixed: t('dashboard.health.mixed', 'mixed'),
    poor: t('dashboard.health.poor', 'poor'),
    poor_contact: t('dashboard.health.poorContact', 'poor contact'),
    calibrating: t('dashboard.health.calibrating', 'calibrating'),
    warming_up: t('dashboard.health.warmingUp', 'warming up'),
    ready: t('dashboard.health.ready', 'ready'),
    receiving: t('dashboard.health.receiving', 'receiving'),
    enabled: t('dashboard.enabled', 'Enabled'),
    disabled: t('dashboard.disabled', 'Disabled'),
    recording: t('dashboard.recording', 'recording'),
    forced: t('dashboard.health.forced', 'forced'),
    stopped: t('dashboard.health.stopped', 'stopped'),
  };
  return escapeHtml(labels[normalized] || value);
}

function formatTimestampAge(timestamp, ageSeconds) {
  if (!timestamp) return '-';
  const age = ageSeconds === null || ageSeconds === undefined ? '' : ` (${formatValue(ageSeconds, ' s')})`;
  return `${escapeHtml(timestamp)}${age}`;
}

function formatCard(client) {
  if (client.current_index === null || client.current_index === undefined) return '-';
  return `#${Number(client.current_index) + 1} ${escapeHtml(client.current_type || '')}`;
}

function formatClientPluginStatus(pluginStatus) {
  if (!pluginStatus || typeof pluginStatus !== 'object' || Array.isArray(pluginStatus)) return '-';
  const rows = Object.entries(pluginStatus).map(([pluginKey, value]) => {
    const label = pluginByKey(pluginKey)?.ui?.label || formatPluginName(pluginKey);
    const status = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const summary = status.message || status.permission || status.state || status.status || '-';
    const warning = status.last_error || status.error || '';
    return `<strong>${escapeHtml(label)}</strong>: ${escapeHtml(summary)}${warning ? `<br><span class="status-warning">${escapeHtml(warning)}</span>` : ''}`;
  });
  return rows.length ? rows.join('<br>') : '-';
}

function formatValue(value, suffix = '') {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = Number(value);
  if (Number.isFinite(numericValue)) return `${numericValue.toFixed(2)}${suffix}`;
  return escapeHtml(value);
}

function formatBoolean(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (['1', 'true', 'yes', 'on', 'present'].includes(normalized)) return t('dashboard.yes', 'yes');
    if (['0', 'false', 'no', 'off', 'absent'].includes(normalized)) return t('dashboard.no', 'no');
  }
  return value ? t('dashboard.yes', 'yes') : t('dashboard.no', 'no');
}

function formatEnabled(value) {
  return escapeHtml(value ? t('dashboard.enabled', 'Enabled') : t('dashboard.disabled', 'Disabled'));
}

function formatOnOff(value) {
  return value ? t('dashboard.on', 'on') : t('dashboard.off', 'off');
}

function formatSensorChannels(source, keys) {
  if (!source || typeof source !== 'object') return '-';
  const parts = keys
    .filter((key) => source[key] !== null && source[key] !== undefined && source[key] !== '')
    .map((key) => `${escapeHtml(key)}: ${formatValue(source[key])}`);
  return parts.length ? parts.join('<br>') : '-';
}

function formatObjectBrief(value) {
  if (!value || typeof value !== 'object' || !Object.keys(value).length) return '-';
  return Object.entries(value)
    .slice(0, 5)
    .map(([key, item]) => `${escapeHtml(key)}: ${escapeHtml(item)}`)
    .join('<br>');
}

function formatPluginName(key) {
  return pluginByKey(key)?.ui?.label
    || String(key || '').replace(/[._-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

window.addEventListener('beforeunload', () => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
  }
});
