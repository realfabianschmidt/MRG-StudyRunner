/**
 * The formatting helpers every sensor tile uses, and the `ui` object handed
 * to plugin dashboard extensions (`renderDashboard(context, ui)`).
 */
import { t } from '../shared/i18n.js';
import { escapeHtml } from '../shared/dom-utils.js';
import { graphSection, infoTip } from '../shared/dashboard-graph.js';
import { pluginByKey } from '../shared/plugin-catalog.js';

export function renderRuntimeButtons(item, compact = false) {
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

export function statusLabel(status) {
  // Operators see a plain-language label; the raw status stays in the
  // CSS class for styling.
  const raw = String(status || 'unknown');
  return t(`dashboard.status.${raw}`, raw.replace(/_/g, ' '));
}

export function fieldLabel(fieldKey, fallback, helpKey = fieldKey) {
  const label = escapeHtml(t(`dashboard.field.${fieldKey}`, fallback));
  const text = t(`dashboard.help.${helpKey}`, '');
  if (!text) return label;
  const safeText = escapeHtml(text);
  const safeLabel = escapeHtml(t('dashboard.helpIconLabel', `{field}`).replace('{field}', fallback));
  return `<span class="status-label-help" tabindex="0" title="${safeText}" aria-label="${safeLabel}: ${safeText}">${label}</span>`;
}

export function formatHealthValue(value) {
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

export function formatTimestampAge(timestamp, ageSeconds) {
  if (!timestamp) return '-';
  const age = ageSeconds === null || ageSeconds === undefined ? '' : ` (${formatValue(ageSeconds, ' s')})`;
  return `${escapeHtml(timestamp)}${age}`;
}

export function formatValue(value, suffix = '') {
  if (value === null || value === undefined || value === '') return '-';
  const numericValue = Number(value);
  if (Number.isFinite(numericValue)) return `${numericValue.toFixed(2)}${suffix}`;
  return escapeHtml(value);
}

export function formatBoolean(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (['1', 'true', 'yes', 'on', 'present'].includes(normalized)) return t('dashboard.yes', 'yes');
    if (['0', 'false', 'no', 'off', 'absent'].includes(normalized)) return t('dashboard.no', 'no');
  }
  return value ? t('dashboard.yes', 'yes') : t('dashboard.no', 'no');
}

export function formatEnabled(value) {
  return escapeHtml(value ? t('dashboard.enabled', 'Enabled') : t('dashboard.disabled', 'Disabled'));
}

export function formatOnOff(value) {
  return value ? t('dashboard.on', 'on') : t('dashboard.off', 'off');
}

export function formatSensorChannels(source, keys) {
  if (!source || typeof source !== 'object') return '-';
  const parts = keys
    .filter((key) => source[key] !== null && source[key] !== undefined && source[key] !== '')
    .map((key) => `${escapeHtml(key)}: ${formatValue(source[key])}`);
  return parts.length ? parts.join('<br>') : '-';
}

export function formatObjectBrief(value) {
  if (!value || typeof value !== 'object' || !Object.keys(value).length) return '-';
  return Object.entries(value)
    .slice(0, 5)
    .map(([key, item]) => `${escapeHtml(key)}: ${escapeHtml(item)}`)
    .join('<br>');
}

export function formatPluginName(key) {
  return pluginByKey(key)?.ui?.label
    || String(key || '').replace(/[._-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function dashboardUiHelpers() {
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
    graphSection,
    infoTip,
  };
}
