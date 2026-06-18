import { getJson, postJson } from './api-client.js';

const POLL_INTERVAL_MS = 2000;

let pollTimer = null;

export function initializeAdminDashboard({ showToast } = {}) {
  const elements = getDashboardElements();
  if (!elements.dashboardButton || !elements.dashboard) {
    return;
  }

  elements.dashboardButton.addEventListener('click', () => showDashboard(elements));
  elements.backButton?.addEventListener('click', () => showEditor(elements));
  elements.dashboard.addEventListener('click', (event) => {
    const button = event.target.closest('[data-dashboard-action]');
    if (button) {
      void runDashboardAction(button.dataset.dashboardAction, elements, showToast);
    }
  });

  const refresh = () => {
    void refreshAdminStatus(elements, showToast);
  };

  refresh();
  pollTimer = window.setInterval(refresh, POLL_INTERVAL_MS);
}

async function runDashboardAction(action, elements, showToast) {
  const toggleMatch = action.match(/^toggle_(.+)_(on|off)$/);
  if (toggleMatch) {
    const [, integrationKey, state] = toggleMatch;
    await updateIntegrationToggle(integrationKey, state === 'on', elements, showToast);
    return;
  }

  const runtimeMatch = action.match(/^runtime_(.+)_(start|stop|restart)$/);
  if (!runtimeMatch) {
    return;
  }

  const [, integrationKey, runtimeAction] = runtimeMatch;
  try {
    await postJson(`/api/admin/integrations/${encodeURIComponent(integrationKey)}/${runtimeAction}`, {});
    showToast?.('Dashboard action sent', 'success');
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Dashboard action failed:', error);
    showToast?.('Dashboard action failed', 'error');
  }
}

async function updateIntegrationToggle(integrationKey, enabled, elements, showToast) {
  try {
    await postJson(`/api/admin/integrations/${encodeURIComponent(integrationKey)}/enabled`, { enabled });
    showToast?.(`${formatIntegrationName(integrationKey)} ${enabled ? 'enabled' : 'disabled'}`, 'success');
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Integration toggle failed:', error);
    showToast?.('Integration toggle failed', 'error');
  }
}

function getDashboardElements() {
  return {
    editView: document.getElementById('admin-edit-view'),
    dashboard: document.getElementById('admin-dashboard'),
    dashboardButton: document.getElementById('btn-admin-dashboard'),
    backButton: document.getElementById('btn-admin-edit-view'),
    clients: document.getElementById('dashboard-clients'),
    brainbit: document.getElementById('dashboard-brainbit'),
    radar: document.getElementById('dashboard-radar'),
    camera: document.getElementById('dashboard-camera'),
    controls: document.getElementById('dashboard-integration-controls'),
    xdf: document.getElementById('dashboard-xdf'),
  };
}

async function refreshAdminStatus(elements, showToast) {
  try {
    const status = await getJson('/api/admin/status');
    renderAdminStatus(elements, status);
  } catch (error) {
    console.error('[admin] Could not load admin status:', error);
    showToast?.('Dashboard status failed', 'error');
  }
}

function renderAdminStatus(elements, status) {
  const clients = status.study_clients || {};
  elements.dashboardButton.hidden = false;

  renderClients(elements.clients, clients.clients || []);
  renderBrainBit(elements.brainbit, status.integrations?.brainbit || {});
  renderMiniRadar(elements.radar, status.integrations?.mini_radar || {});
  renderCameraEmotion(elements.camera, status.integrations?.camera_emotion || {}, status.integrations?.emotion_worker || {});
  renderIntegrationControls(elements.controls, status.integrations || {});
  renderXdf(elements.xdf, status);
}

function renderClients(target, clients) {
  if (!target) return;
  if (!clients.length) {
    target.innerHTML = '<p>No connected study client yet.</p>';
    return;
  }

  target.innerHTML = clients.map((client) => `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(client.status)}">${escapeHtml(client.status)}</span>
      <strong>${escapeHtml(client.participant_id || 'No participant ID yet')}</strong>
    </div>
    <dl class="status-list">
      <dt>Study</dt><dd>${escapeHtml(client.study_id || '-')}</dd>
      <dt>Card</dt><dd>${formatCard(client)}</dd>
      <dt>Age</dt><dd>${escapeHtml(client.age_seconds)}s</dd>
      <dt>Camera</dt><dd>${escapeHtml(client.camera_permission || 'unknown')}</dd>
    </dl>
  `).join('');
}

function renderBrainBit(target, brainbit) {
  if (!target) return;
  const latest = brainbit.latest || {};
  const battery = latest.battery || {};
  const quality = latest.quality || {};
  const bands = latest.bands || {};
  const mental = latest.mental || {};
  const calibration = latest.calibration || {};
  const sensorState = latest.sensor_state || {};

  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(brainbit.status || 'unknown')}">${escapeHtml(brainbit.status || 'unknown')}</span>
      <strong>${brainbit.configured_enabled ? 'Enabled' : 'Disabled'}</strong>
    </div>
    <dl class="status-list">
      <dt>Scan window</dt><dd>${formatValue(brainbit.scan_timeout_seconds, ' s')} (${escapeHtml(brainbit.scan_mode || 'one-shot')})</dd>
      <dt>Last scan</dt><dd>${escapeHtml(brainbit.last_scan_started_at || '-')}</dd>
      <dt>Battery</dt><dd>${formatValue(battery.percent, '%')}</dd>
      <dt>Quality</dt><dd>${formatSensorChannels(quality, ['O1', 'O2', 'T3', 'T4'])}</dd>
      <dt>Bands</dt><dd>${formatSensorChannels(bands, ['delta', 'theta', 'alpha', 'beta', 'gamma'])}</dd>
      <dt>Mental</dt><dd>${formatSensorChannels(mental, ['Inst_Attention', 'Inst_Relaxation', 'Rel_Attention', 'Rel_Relaxation'])}</dd>
      <dt>Calibration</dt><dd>${formatCalibration(calibration)}</dd>
      <dt>State</dt><dd>${formatObjectBrief(sensorState)}</dd>
      <dt>LSL</dt><dd>${brainbit.lsl_enabled ? 'Enabled' : 'Disabled'}</dd>
      <dt>Last active</dt><dd>${escapeHtml(latest.last_activity_at || brainbit.last_activity_at || '-')}</dd>
      <dt>Message</dt><dd>${escapeHtml(latest.last_message || brainbit.last_message || '-')}</dd>
    </dl>
    ${renderRuntimeButtons(brainbit)}
  `;
}

function renderMiniRadar(target, radar) {
  if (!target) return;
  const latest = radar.latest || {};
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(radar.status || 'planned')}">${escapeHtml(radar.status || 'planned')}</span>
      <strong>${radar.configured_enabled ? 'Enabled' : 'Disabled'}</strong>
    </div>
    <dl class="status-list">
      <dt>Connection</dt><dd>${escapeHtml(radar.connection_type || '-')}</dd>
      <dt>Device</dt><dd>${escapeHtml(radar.device_label || radar.ble_device_name || radar.port || '-')}</dd>
      <dt>Scan window</dt><dd>${formatValue(radar.scan_timeout_seconds, ' s')} (${escapeHtml(radar.scan_mode || 'repeated')})</dd>
      <dt>Last scan</dt><dd>${escapeHtml(radar.last_scan_started_at || '-')}</dd>
      <dt>Next retry</dt><dd>${escapeHtml(radar.next_retry_at || '-')}</dd>
      <dt>Heart</dt><dd>${formatValue(latest.heartRate, ' BPM')}</dd>
      <dt>Breath</dt><dd>${formatValue(latest.breathRate, ' /min')}</dd>
      <dt>Presence</dt><dd>${formatBoolean(latest.present)}</dd>
      <dt>Valid</dt><dd>${formatBoolean(latest.valid)}</dd>
      <dt>Stabilized</dt><dd>${formatBoolean(latest.stabilized)}</dd>
      <dt>Distance</dt><dd>${formatValue(latest.distance, ' cm')}</dd>
      <dt>Phases</dt><dd>${formatPhases(latest)}</dd>
      <dt>Sequence</dt><dd>${escapeHtml(latest.sequence_number ?? '-')}</dd>
      <dt>Drops</dt><dd>${formatDropInfo(latest)}</dd>
      <dt>Timing</dt><dd>${formatTimingInfo(latest)}</dd>
      <dt>Last update age</dt><dd>${formatValue(radar.seconds_since_last_activity, ' s')}</dd>
      <dt>Message</dt><dd>${escapeHtml(radar.last_message || '-')}</dd>
    </dl>
    ${renderRuntimeButtons(radar)}
  `;
}

function renderCameraEmotion(target, camera, worker) {
  if (!target) return;
  const latest = camera.latest || {};
  const analysis = latest.analysis || {};
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(camera.status || 'planned')}">${escapeHtml(camera.status || 'planned')}</span>
      <strong>${camera.configured_enabled ? 'Enabled' : 'Disabled'}</strong>
    </div>
    <dl class="status-list">
      <dt>Mode</dt><dd>${escapeHtml(camera.worker_mode || '-')}</dd>
      <dt>Interval</dt><dd>${formatValue(camera.snapshot_interval_ms, ' ms')}</dd>
      <dt>Worker</dt><dd>${escapeHtml(worker.status || '-')}</dd>
      <dt>Emotion</dt><dd>${escapeHtml(analysis.emotion || '-')}</dd>
      <dt>Confidence</dt><dd>${formatValue(analysis.confidence)}</dd>
      <dt>Face</dt><dd>${formatBoolean(analysis.face_detected)}</dd>
      <dt>Frame</dt><dd>${formatFrame(latest.frame)}</dd>
      <dt>Processed</dt><dd>${escapeHtml(latest.processed_at || '-')}</dd>
    </dl>
    ${renderRuntimeButtons(camera)}
  `;
}

function renderIntegrationControls(target, integrations) {
  if (!target) return;
  const rows = Object.values(integrations).filter((item) => item && item.key);
  if (!rows.length) {
    target.innerHTML = '<p>No integrations registered.</p>';
    return;
  }
  target.innerHTML = `<div class="integration-controls">
    ${rows.map(renderIntegrationControlRow).join('')}
  </div>`;
}

function renderIntegrationControlRow(item) {
  const configured = Boolean(item.configured_enabled ?? item.enabled);
  const status = item.status || (configured ? 'enabled' : 'disabled');
  const toggleButtons = item.can_toggle ? `
    <button type="button" class="btn-secondary btn-xs" data-dashboard-action="toggle_${escapeHtml(item.key)}_on"${configured ? ' disabled' : ''}>Enable</button>
    <button type="button" class="btn-secondary btn-xs" data-dashboard-action="toggle_${escapeHtml(item.key)}_off"${!configured ? ' disabled' : ''}>Disable</button>
  ` : '<span class="integration-control-note">Managed by parent integration</span>';

  return `
    <div class="integration-control-row">
      <div class="integration-control-main">
        <span class="status-pill status-pill--${escapeHtml(status)}">${escapeHtml(status)}</span>
        <div>
          <strong>${escapeHtml(item.label || item.key)}</strong>
          <span>${buildIntegrationDetail(item)}</span>
        </div>
      </div>
      <div class="integration-control-actions">
        ${toggleButtons}
        ${renderRuntimeButtons(item, true)}
      </div>
    </div>`;
}

function renderRuntimeButtons(item, compact = false) {
  const buttons = [];
  if (item.can_start) buttons.push(['start', 'Start']);
  if (item.can_restart) buttons.push(['restart', 'Restart']);
  if (item.can_stop) buttons.push(['stop', 'Stop']);
  if (!buttons.length) return compact ? '' : '<div class="dashboard-actions"></div>';
  const className = compact ? 'btn-secondary btn-xs' : 'btn-secondary';
  const html = buttons.map(([action, label]) => `
    <button type="button" class="${className}" data-dashboard-action="runtime_${escapeHtml(item.key)}_${action}">${label}</button>
  `).join('');
  return compact ? html : `<div class="dashboard-actions">${html}</div>`;
}

function buildIntegrationDetail(item) {
  const details = [];
  if (item.category) details.push(item.category);
  if (item.device_label) details.push(item.device_label);
  if (item.lsl_enabled !== undefined) details.push(`LSL ${item.lsl_enabled ? 'on' : 'off'}`);
  if (item.recording_enabled !== undefined) details.push(`recording ${item.recording_enabled ? 'on' : 'off'}`);
  if (item.scan_timeout_seconds !== undefined && item.scan_timeout_seconds !== null) details.push(`scan ${item.scan_timeout_seconds}s`);
  if (item.url) details.push(item.url);
  if (item.host) details.push(`${item.host}:${item.port || ''}`);
  return escapeHtml(details.filter(Boolean).join(' - ') || item.last_message || '-');
}

function renderXdf(target, status) {
  if (!target) return;
  const labrecorder = status.integrations?.labrecorder || {};
  const lsl = status.integrations?.lsl || {};
  target.innerHTML = `
    <dl class="status-list">
      <dt>Primary sync</dt><dd>${escapeHtml(status.timestamp_strategy?.primary || 'LSL')}</dd>
      <dt>Format</dt><dd>${escapeHtml(status.timestamp_strategy?.recording_format || '.xdf')}</dd>
      <dt>LSL markers</dt><dd>${lsl.configured_enabled ? 'Enabled' : 'Disabled'}</dd>
      <dt>LabRecorder</dt><dd>${labrecorder.configured_enabled ? 'Enabled' : 'Disabled'}</dd>
    </dl>
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

function formatCard(client) {
  if (client.current_index === null || client.current_index === undefined) return '-';
  return `#${Number(client.current_index) + 1} ${escapeHtml(client.current_type || '')}`;
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
    if (['1', 'true', 'yes', 'on', 'present'].includes(normalized)) return 'yes';
    if (['0', 'false', 'no', 'off', 'absent'].includes(normalized)) return 'no';
  }
  return value ? 'yes' : 'no';
}

function formatSensorChannels(source, keys) {
  if (!source || typeof source !== 'object') return '-';
  const parts = keys
    .filter((key) => source[key] !== null && source[key] !== undefined && source[key] !== '')
    .map((key) => `${escapeHtml(key)}: ${formatValue(source[key])}`);
  return parts.length ? parts.join('<br>') : '-';
}

function formatCalibration(calibration) {
  if (!calibration || typeof calibration !== 'object' || !Object.keys(calibration).length) return '-';
  const parts = [];
  if (calibration.event) parts.push(escapeHtml(calibration.event));
  if (calibration.progress_percent !== null && calibration.progress_percent !== undefined) {
    parts.push(`${formatValue(calibration.progress_percent, '%')}`);
  }
  if (calibration.stage) parts.push(`stage ${escapeHtml(calibration.stage)}`);
  return parts.length ? parts.join(', ') : formatObjectBrief(calibration);
}

function formatObjectBrief(value) {
  if (!value || typeof value !== 'object' || !Object.keys(value).length) return '-';
  return Object.entries(value)
    .slice(0, 5)
    .map(([key, item]) => `${escapeHtml(key)}: ${escapeHtml(item)}`)
    .join('<br>');
}

function formatPhases(latest) {
  const parts = [
    ['heart', latest.heartPhase],
    ['breath', latest.breathPhase],
    ['total', latest.totalPhase],
  ]
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${key}: ${formatValue(value)}`);
  return parts.length ? parts.join('<br>') : '-';
}

function formatDropInfo(latest) {
  const dropped = latest.dropped_since_previous ?? '-';
  const total = latest.total_dropped ?? '-';
  return `${escapeHtml(dropped)} last / ${escapeHtml(total)} total`;
}

function formatTimingInfo(latest) {
  const parts = [];
  if (latest.device_interval_ms !== null && latest.device_interval_ms !== undefined) parts.push(`device ${formatValue(latest.device_interval_ms, ' ms')}`);
  if (latest.host_interval_ms !== null && latest.host_interval_ms !== undefined) parts.push(`host ${formatValue(latest.host_interval_ms, ' ms')}`);
  if (latest.jitter_ms !== null && latest.jitter_ms !== undefined) parts.push(`jitter ${formatValue(latest.jitter_ms, ' ms')}`);
  return parts.length ? parts.join('<br>') : '-';
}

function formatFrame(frame) {
  if (!frame) return '-';
  const width = frame.width || '-';
  const height = frame.height || '-';
  return `${escapeHtml(width)} x ${escapeHtml(height)}`;
}

function formatIntegrationName(key) {
  const names = {
    brainbit: 'BrainBit',
    mini_radar: 'MR60 BLE',
    camera_emotion: 'Camera emotion',
    lsl: 'LSL',
    labrecorder: 'LabRecorder',
    osc: 'OSC',
    notion: 'Notion',
  };
  return names[key] || key;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

window.addEventListener('beforeunload', () => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
  }
});
