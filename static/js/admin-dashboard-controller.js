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
  elements.settingsButton?.addEventListener('click', () => openSettings(elements));
  elements.closeSettingsButton?.addEventListener('click', () => closeSettings(elements));
  elements.reloadHardwareConfigButton?.addEventListener('click', () => {
    void loadHardwareConfig(elements, showToast);
  });
  elements.saveHardwareConfigButton?.addEventListener('click', () => {
    void saveHardwareConfig(elements, showToast);
  });
  elements.dashboard.addEventListener('click', (event) => {
    const button = event.target.closest('[data-dashboard-action]');
    if (button) {
      void runDashboardAction(button.dataset.dashboardAction, elements, showToast);
    }
  });
  elements.settingsModal?.addEventListener('click', (event) => {
    if (event.target === elements.settingsModal) {
      closeSettings(elements);
    }
  });

  const refresh = () => {
    void refreshAdminStatus(elements, showToast);
  };

  refresh();
  pollTimer = window.setInterval(refresh, POLL_INTERVAL_MS);
}

async function runDashboardAction(action, elements, showToast) {
  const routes = {
    radar_start:   '/api/admin/radar/start',
    radar_stop:    '/api/admin/radar/stop',
    radar_restart: '/api/admin/radar/restart',
    camera_start:  '/api/admin/camera/start',
    camera_stop:   '/api/admin/camera/stop',
  };

  // RPi sensor actions have the form raspi_{sensor}_{action}
  const raspiMatch = action.match(/^raspi_(\w+)_(start|stop|restart)$/);
  if (raspiMatch) {
    const [, sensor, cmd] = raspiMatch;
    try {
      await postJson(`/api/raspi/${cmd}`, { sensor });
      if (typeof showToast === 'function') showToast(`RPi ${sensor} ${cmd}`, 'success');
      await refreshAdminStatus(elements, showToast);
    } catch (error) {
      console.error('[admin] RPi action failed:', error);
      if (typeof showToast === 'function') showToast('RPi action failed', 'error');
    }
    return;
  }
  const route = routes[action];
  if (!route) {
    return;
  }

  try {
    await postJson(route, {});
    if (typeof showToast === 'function') {
      showToast('Dashboard action sent', 'success');
    }
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Dashboard action failed:', error);
    if (typeof showToast === 'function') {
      showToast('Dashboard action failed', 'error');
    }
  }
}

function getDashboardElements() {
  return {
    editView: document.getElementById('admin-edit-view'),
    dashboard: document.getElementById('admin-dashboard'),
    dashboardButton: document.getElementById('btn-admin-dashboard'),
    backButton: document.getElementById('btn-admin-edit-view'),
    settingsButton: document.getElementById('btn-admin-settings'),
    settingsModal: document.getElementById('admin-settings-modal'),
    closeSettingsButton: document.getElementById('btn-close-settings'),
    reloadHardwareConfigButton: document.getElementById('btn-reload-hardware-config'),
    saveHardwareConfigButton: document.getElementById('btn-save-hardware-config'),
    hardwareConfigEditor: document.getElementById('hardware-config-editor'),
    hardwareConfigStatus: document.getElementById('hardware-config-status'),
    clients: document.getElementById('dashboard-clients'),
    brainbit: document.getElementById('dashboard-brainbit'),
    radar: document.getElementById('dashboard-radar'),
    camera: document.getElementById('dashboard-camera'),
    raspi: document.getElementById('dashboard-raspi'),
    xdf: document.getElementById('dashboard-xdf'),
  };
}

async function refreshAdminStatus(elements, showToast) {
  try {
    const status = await getJson('/api/admin/status');
    renderAdminStatus(elements, status);
  } catch (error) {
    console.error('[admin] Could not load admin status:', error);
    if (typeof showToast === 'function') {
      showToast('Dashboard status failed', 'error');
    }
  }
}

function renderAdminStatus(elements, status) {
  const clients = status.study_clients || {};
  elements.dashboardButton.hidden = !clients.dashboard_available;

  if (!clients.dashboard_available && !elements.dashboard.hidden) {
    showEditor(elements);
  }

  renderClients(elements.clients, clients.clients || []);
  renderBrainBit(elements.brainbit, status.integrations?.brainbit || {});
  renderMiniRadar(elements.radar, status.integrations?.mini_radar || {});
  renderCameraEmotion(elements.camera, status.integrations?.camera_emotion || {});
  renderRaspi(elements.raspi, status.integrations?.raspi || {});
  renderXdf(elements.xdf, status);
}

function renderClients(target, clients) {
  if (!target) {
    return;
  }
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
  if (!target) {
    return;
  }
  const latest = brainbit.latest || {};
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(brainbit.status || 'unknown')}">${escapeHtml(brainbit.status || 'unknown')}</span>
      <strong>${brainbit.enabled ? 'Enabled' : 'Disabled'}</strong>
    </div>
    <dl class="status-list">
      <dt>Battery</dt><dd>${escapeHtml(latest.battery?.percent ?? '-')}</dd>
      <dt>Last active</dt><dd>${escapeHtml(latest.last_activity_at || '-')}</dd>
      <dt>Message</dt><dd>${escapeHtml(latest.last_message || '-')}</dd>
    </dl>
  `;
}

function renderMiniRadar(target, radar) {
  if (!target) {
    return;
  }
  const latest = radar.latest || {};
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(radar.status || 'planned')}">${escapeHtml(radar.status || 'planned')}</span>
      <strong>${radar.enabled ? 'Enabled' : 'Disabled'}</strong>
    </div>
    <dl class="status-list">
      <dt>Heart</dt><dd>${formatValue(latest.heartRate, ' BPM')}</dd>
      <dt>Breath</dt><dd>${formatValue(latest.breathRate, ' /min')}</dd>
      <dt>Presence</dt><dd>${escapeHtml(latest.present ?? '-')}</dd>
      <dt>Quality</dt><dd>${formatValue(latest.quality)}</dd>
      <dt>Last active</dt><dd>${escapeHtml(radar.last_activity_at || '-')}</dd>
    </dl>
    <div class="dashboard-actions">
      <button type="button" class="btn-secondary" data-dashboard-action="radar_start">Start</button>
      <button type="button" class="btn-secondary" data-dashboard-action="radar_restart">Restart</button>
      <button type="button" class="btn-secondary" data-dashboard-action="radar_stop">Stop</button>
    </div>
  `;
}

function renderCameraEmotion(target, camera) {
  if (!target) {
    return;
  }
  const latest = camera.latest || {};
  const analysis = latest.analysis || {};
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(camera.status || 'planned')}">${escapeHtml(camera.status || 'planned')}</span>
      <strong>${camera.enabled ? 'Enabled' : 'Disabled'}</strong>
    </div>
    <dl class="status-list">
      <dt>Emotion</dt><dd>${escapeHtml(analysis.emotion || '-')}</dd>
      <dt>Confidence</dt><dd>${formatValue(analysis.confidence)}</dd>
      <dt>Face</dt><dd>${escapeHtml(analysis.face_detected ?? '-')}</dd>
      <dt>Frame</dt><dd>${formatFrame(latest.frame)}</dd>
      <dt>Processed</dt><dd>${escapeHtml(latest.processed_at || '-')}</dd>
    </dl>
    <div class="dashboard-actions">
      <button type="button" class="btn-secondary" data-dashboard-action="camera_start">Start</button>
      <button type="button" class="btn-secondary" data-dashboard-action="camera_stop">Stop</button>
    </div>
  `;
}

function renderRaspi(target, raspi) {
  if (!target) return;

  if (!raspi.enabled) {
    target.innerHTML = `
      <div class="status-row">
        <span class="status-pill status-pill--disabled">disabled</span>
        <strong>Raspberry Pi Gateway</strong>
      </div>
      <p style="font-size:.8rem;color:var(--ink-40);margin:.5rem 0 0;">
        Enable in hardware_config.json → raspi.enabled
      </p>`;
    return;
  }

  const connected = raspi.connected;
  const sensors = raspi.sensors || {};
  const SENSOR_LABELS = { brainbit: 'BrainBit', emg: 'EMG', radar: 'Radar', camera: 'Camera' };

  const sensorRows = Object.entries(SENSOR_LABELS).map(([key, label]) => {
    const s = sensors[key] || {};
    const status = s.status || 'stopped';
    const running = status === 'running' || status === 'connected';
    return `
      <div class="raspi-sensor-row">
        <span class="raspi-sensor-name">${escapeHtml(label)}</span>
        <span class="status-pill status-pill--${escapeHtml(status)}">${escapeHtml(status)}</span>
        <div class="dashboard-actions" style="margin:0;gap:4px;">
          <button type="button" class="btn-secondary btn-xs"
                  data-dashboard-action="raspi_${key}_start">Start</button>
          <button type="button" class="btn-secondary btn-xs"
                  data-dashboard-action="raspi_${key}_restart">↺</button>
          <button type="button" class="btn-secondary btn-xs"
                  data-dashboard-action="raspi_${key}_stop">Stop</button>
        </div>
        ${s.last_seen ? `<span class="raspi-last-seen">${escapeHtml(s.last_seen)}</span>` : ''}
      </div>`;
  }).join('');

  target.innerHTML = `
    <div class="status-row">
      <span class="raspi-dot raspi-dot--${connected ? 'ok' : 'err'}"></span>
      <strong>Raspberry Pi ${connected ? 'connected' : 'unreachable'}</strong>
    </div>
    <div class="raspi-sensors">${sensorRows}</div>`;
}

function renderXdf(target, status) {
  if (!target) {
    return;
  }
  const labrecorder = status.integrations?.labrecorder || {};
  target.innerHTML = `
    <dl class="status-list">
      <dt>Primary sync</dt><dd>${escapeHtml(status.timestamp_strategy?.primary || 'LSL')}</dd>
      <dt>Format</dt><dd>${escapeHtml(status.timestamp_strategy?.recording_format || '.xdf')}</dd>
      <dt>LabRecorder</dt><dd>${labrecorder.enabled ? 'Enabled' : 'Disabled'}</dd>
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

function openSettings(elements) {
  if (elements.settingsModal) {
    elements.settingsModal.hidden = false;
  }
  if (elements.hardwareConfigEditor && !elements.hardwareConfigEditor.value.trim()) {
    void loadHardwareConfig(elements);
  }
}

function closeSettings(elements) {
  if (elements.settingsModal) {
    elements.settingsModal.hidden = true;
  }
}

async function loadHardwareConfig(elements, showToast) {
  if (!elements.hardwareConfigEditor) {
    return;
  }

  setHardwareConfigStatus(elements, 'Loading hardware_config.json ...');
  try {
    const config = await getJson('/api/hardware-config');
    elements.hardwareConfigEditor.value = `${JSON.stringify(config, null, 2)}\n`;
    setHardwareConfigStatus(elements, 'Loaded hardware_config.json.');
  } catch (error) {
    console.error('[admin] Could not load hardware config:', error);
    setHardwareConfigStatus(elements, `Could not load hardware_config.json: ${error.message}`);
    if (typeof showToast === 'function') {
      showToast('Hardware config load failed', 'error');
    }
  }
}

async function saveHardwareConfig(elements, showToast) {
  if (!elements.hardwareConfigEditor) {
    return;
  }

  let config;
  try {
    config = JSON.parse(elements.hardwareConfigEditor.value);
  } catch (error) {
    setHardwareConfigStatus(elements, `Invalid JSON: ${error.message}`);
    if (typeof showToast === 'function') {
      showToast('Hardware config JSON is invalid', 'error');
    }
    return;
  }

  setHardwareConfigStatus(elements, 'Saving hardware_config.json ...');
  try {
    const response = await postJson('/api/hardware-config', config);
    const statusMessage = response?.raspi?.ok === false
      ? `${response.message || 'Saved.'} Raspberry Pi push failed: ${response.raspi.message}`
      : (response.message || 'Saved. Restart the server to apply startup integration changes.');
    setHardwareConfigStatus(elements, statusMessage);
    if (typeof showToast === 'function') {
      showToast(response?.raspi?.ok === false ? 'Saved, but Pi push failed' : 'Hardware config saved', response?.raspi?.ok === false ? 'error' : 'success');
    }
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Could not save hardware config:', error);
    setHardwareConfigStatus(elements, `Could not save hardware_config.json: ${error.message}`);
    if (typeof showToast === 'function') {
      showToast('Hardware config save failed', 'error');
    }
  }
}

function setHardwareConfigStatus(elements, message) {
  if (elements.hardwareConfigStatus) {
    elements.hardwareConfigStatus.textContent = message;
  }
}

function formatCard(client) {
  if (client.current_index === null || client.current_index === undefined) {
    return '-';
  }
  return `#${Number(client.current_index) + 1} ${escapeHtml(client.current_type || '')}`;
}

function formatValue(value, suffix = '') {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const numericValue = Number(value);
  if (Number.isFinite(numericValue)) {
    return `${numericValue.toFixed(2)}${suffix}`;
  }
  return escapeHtml(value);
}

function formatFrame(frame) {
  if (!frame) {
    return '-';
  }
  const width = frame.width || '-';
  const height = frame.height || '-';
  return `${escapeHtml(width)} x ${escapeHtml(height)}`;
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
