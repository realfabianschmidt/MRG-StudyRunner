/** Dashboard view of participant connections; selection remains a server decision. */
import { t } from '../shared/i18n.js';
import { escapeHtml } from '../shared/dom-utils.js';
import { pluginByKey } from '../shared/plugin-catalog.js';
import { fieldLabel, formatPluginName, statusLabel } from './dashboard-ui-helpers.js';

export const PARTICIPANT_TILE_KEY = 'panel_participant_clients';

export function renderParticipantTile(root, status) {
  let tile = [...root.querySelectorAll('[data-plugin-tile]')]
    .find((node) => node.dataset.pluginTile === PARTICIPANT_TILE_KEY);
  if (!tile) {
    tile = document.createElement('article');
    tile.className = 'dashboard-card sensor-tile';
    tile.dataset.pluginTile = PARTICIPANT_TILE_KEY;
    tile.innerHTML = `
      <div class="dashboard-card-title" data-tile-handle tabindex="0" role="group"
        title="${escapeHtml(t('dashboard.tiles.handle', 'Drag to move this tile; arrow keys also work'))}">
        <i class="iconoir-menu-scale tile-grip" aria-hidden="true"></i>
        <i class="iconoir-smartphone-device"></i>
        <span>${escapeHtml(t('dashboard.studyClient', 'Study client'))}</span>
      </div>
      <div class="dashboard-card-body" data-participant-clients></div>`;
  }
  renderClients(tile.querySelector('[data-participant-clients]'), status.study_clients || {}, status.study_run_state || {});
  return tile;
}

export function renderClients(target, status, runState) {
  if (!target) return;
  const clients = status.clients || [];
  const gate = status.single_tablet || {};
  const running = runState.status === 'running';
  const assigned = running ? runState.active_client_id || ''
    : runState.selected_client_id || gate.selected_client_id || '';
  if (!clients.length) {
    target.innerHTML = `<p>${escapeHtml(t('dashboard.noClient', 'No connected study client yet.'))}</p>`;
    return;
  }
  target.innerHTML = clients.map((client) => `
    <div class="status-row participant-device-row">
      <span class="status-pill status-pill--${escapeHtml(client.status)}">${escapeHtml(statusLabel(client.status))}</span>
      <strong>${escapeHtml(client.display_id || '-')}</strong>
      ${client.client_id === assigned ? `<span>${escapeHtml(running
        ? gate.observed ? t('dashboard.device.seen', 'Start seen on device') : t('dashboard.device.awaitingAck', 'Waiting for device acknowledgement')
        : t('dashboard.device.selectedForStart', 'Selected for Start'))}</span>` : ''}
      <button type="button" class="btn-secondary btn-xs" data-participant-target="${escapeHtml(client.client_id)}"
        ${running || client.status !== 'active' || !client.waiting_for_admin_start || client.study_id !== runState.study_id ? 'disabled' : ''}>
        ${escapeHtml(client.client_id === assigned ? t('dashboard.device.selected', 'Selected') : t('dashboard.device.startHere', 'Start here'))}
      </button>
    </div>
    <dl class="status-list">
      <dt>${fieldLabel('study', 'Study')}</dt><dd>${escapeHtml(client.study_id || '-')}</dd>
      <dt>${fieldLabel('phase', 'Phase')}</dt><dd>${escapeHtml(client.participant_phase || (client.waiting_for_admin_start ? 'waiting' : 'loading'))}</dd>
      <dt>${fieldLabel('card', 'Card')}</dt><dd>${formatCard(client)}</dd>
      <dt>${fieldLabel('age', 'Age')}</dt><dd>${escapeHtml(client.age_seconds)}s</dd>
      <dt>${fieldLabel('plugins', 'Plugins')}</dt><dd>${formatClientPluginStatus(client.plugin_status)}</dd>
    </dl>
  `).join('') + (!running && runState.selected_client_id
    ? `<button type="button" class="btn-secondary btn-xs" data-participant-target="">${escapeHtml(t('dashboard.device.clear', 'Clear selection'))}</button>` : '');
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
