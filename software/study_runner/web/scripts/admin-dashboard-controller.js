import { getJson, postJson } from './api-client.js';

import { t } from './i18n.js';

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
  if (action === 'brainbit_select') {
    await selectBrainBitCandidate(button, elements, showToast);
    return;
  }
  if (action === 'emotion_worker_repair_runtime' || action === 'emotion_worker_install_deps') {
    await repairEmotionWorkerRuntime(elements, showToast);
    return;
  }
  if (action === 'reset_sensor_overrides') {
    await resetSensorOverrides(elements, showToast);
    return;
  }
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
    const response = await postJson(`/api/admin/integrations/${encodeURIComponent(integrationKey)}/${runtimeAction}`, {});
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

async function selectBrainBitCandidate(button, elements, showToast) {
  if (!button) return;
  const rawIndex = button.dataset.deviceIndex || '';
  const payload = {
    index: rawIndex === '' ? null : Number(rawIndex),
    name: button.dataset.deviceName || '',
    address: button.dataset.deviceAddress || '',
    serial_number: button.dataset.serialNumber || '',
  };
  try {
    const response = await postJson('/api/admin/brainbit/select-device', payload);
    if (response?.study_controlled) {
      showToast?.(t('dashboard.studyControlledWarning', 'Temporary dashboard overrides can overrule the study settings during this server session.'), 'warning');
      await refreshAdminStatus(elements, showToast);
      return;
    }
    showToast?.(t('dashboard.brainbitBandSaved', 'BrainBit band saved and restart requested'), 'success');
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] BrainBit band selection failed:', error);
    showToast?.(t('dashboard.brainbitBandSaveFailed', 'BrainBit band selection failed'), 'error');
  }
}

async function repairEmotionWorkerRuntime(elements, showToast) {
  try {
    const response = await postJson('/api/admin/emotion-worker/repair-runtime', {});
    const installState = response?.dependency_install || {};
    const modelState = response?.model_asset_install || {};
    showToast?.(
      modelState.last_message || installState.last_message || t('dashboard.runtimeRepairStarted', 'DeepFace runtime repair started'),
      'info',
    );
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Emotion worker runtime repair failed:', error);
    showToast?.(error.message || t('dashboard.runtimeRepairFailed', 'DeepFace runtime repair failed'), 'error');
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

async function updateIntegrationToggle(integrationKey, enabled, elements, showToast) {
  try {
    const response = await postJson(`/api/admin/integrations/${encodeURIComponent(integrationKey)}/enabled`, { enabled });
    const messageKey = enabled ? 'dashboard.integrationEnabled' : 'dashboard.integrationDisabled';
    const fallback = enabled ? '{name} enabled' : '{name} disabled';
    showToast?.(t(messageKey, fallback).replace('{name}', formatIntegrationName(integrationKey)), 'success');
    await refreshAdminStatus(elements, showToast);
  } catch (error) {
    console.error('[admin] Integration toggle failed:', error);
    showToast?.(t('dashboard.integrationToggleFailed', 'Integration toggle failed'), 'error');
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
    const [status, runtimeInfo] = await Promise.all([
      getJson('/api/admin/status'),
      getJson('/api/runtime-info'),
    ]);
    let cameraLive = {};
    try {
      cameraLive = await getJson('/api/admin/camera/live/status');
    } catch (liveError) {
      console.warn('[admin] Could not load camera live monitor status:', liveError);
    }
    status.runtime_info = runtimeInfo;
    status.camera_live = cameraLive;
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
  renderBrainBit(elements.brainbit, status.integrations?.brainbit || {});
  renderMiniRadar(elements.radar, status.integrations?.mini_radar || {});
  renderCameraEmotion(
    elements.camera,
    status.integrations?.camera_emotion || {},
    status.integrations?.emotion_worker || {},
    status.camera_live || {},
    status.runtime_info || {},
    status.sensor_runtime || {},
  );
  renderIntegrationControls(elements.controls, status.integrations || {}, status);
  renderXdf(elements.xdf, status);
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
      <dt>${fieldLabel('camera', 'Camera')}</dt><dd>${escapeHtml(client.camera_permission || t('dashboard.unknown', 'unknown'))}</dd>
      <dt>${fieldLabel('cameraMonitor', 'Camera monitor')}</dt><dd>${formatCameraMonitorClient(client)}</dd>
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
  const contactState = brainbit.contact_quality_state || latest.contact_quality_state || brainbit.health?.contact || 'unknown';

  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(brainbit.status || 'unknown')}">${escapeHtml(statusLabel(brainbit.status))}</span>
      <strong>${formatEnabled(brainbit.configured_enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${fieldLabel('scanWindow', 'Scan window')}</dt><dd>${formatValue(brainbit.scan_timeout_seconds, ' s')} (${escapeHtml(brainbit.scan_mode || 'one-shot')})</dd>
      <dt>${fieldLabel('lastScan', 'Last scan')}</dt><dd>${escapeHtml(brainbit.last_scan_started_at || '-')}</dd>
      <dt>${fieldLabel('band', 'Band')}</dt><dd>${renderBrainBitBand(brainbit)}</dd>
      <dt>${fieldLabel('battery', 'Battery')}</dt><dd>${formatValue(battery.percent, '%')}</dd>
      <dt>${fieldLabel('quality', 'Quality')}</dt><dd>${formatQualityWithContact(quality, contactState)}</dd>
      <dt>${fieldLabel('bands', 'Bands')}</dt><dd>${formatSensorChannels(bands, ['delta', 'theta', 'alpha', 'beta', 'gamma'])}</dd>
      <dt>${fieldLabel('mental', 'Mental')}</dt><dd>${formatSensorChannels(mental, ['Inst_Attention', 'Inst_Relaxation', 'Rel_Attention', 'Rel_Relaxation'])}</dd>
      <dt>${fieldLabel('calibration', 'Calibration')}</dt><dd>${formatCalibration(calibration)}</dd>
      <dt>${fieldLabel('brainbitDataLsl', 'BrainBit data LSL')}</dt><dd>${formatEnabled(brainbit.lsl_enabled)}</dd>
      <dt>${fieldLabel('touchdesigner', 'TouchDesigner')}</dt><dd>${formatEnabled(brainbit.touchdesigner_forwarding_enabled)}</dd>
      <dt>${fieldLabel('lastActive', 'Last active')}</dt><dd>${formatTimestampAge(latest.last_activity_at || brainbit.last_activity_at, brainbit.seconds_since_last_activity)}</dd>
      <dt>${fieldLabel('message', 'Message')}</dt><dd>${formatBrainBitMessage(brainbit, latest)}</dd>
    </dl>
    ${renderRuntimeButtons(brainbit)}
  `;
}

function formatBrainBitMessage(brainbit, latest) {
  // The adapter tags known failures (headset off, Bluetooth off, missing
  // libraries) with a key so the operator sees a translated sentence instead of
  // the raw English fallback.
  const fallback = latest.last_message || brainbit.last_message || '-';
  const detailKey = latest.status_detail_key || brainbit.status_detail_key;
  const hintKey = latest.status_detail_hint_key || brainbit.status_detail_hint_key;
  const message = detailKey ? t(detailKey, fallback) : fallback;
  const hint = hintKey ? t(hintKey, '') : '';
  if (!hint) return escapeHtml(message);
  return `${escapeHtml(message)}<br><span class="status-muted">${escapeHtml(hint)}</span>`;
}

function renderMiniRadar(target, radar) {
  if (!target) return;
  const latest = radar.latest || {};
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(radar.status || 'unknown')}">${escapeHtml(statusLabel(radar.status))}</span>
      <strong>${formatEnabled(radar.configured_enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${fieldLabel('connection', 'Connection')}</dt><dd>${escapeHtml(radar.connection_type || '-')}</dd>
      <dt>${fieldLabel('device', 'Device')}</dt><dd>${escapeHtml(radar.device_label || radar.ble_device_name || radar.port || '-')}</dd>
      <dt>${fieldLabel('scanWindow', 'Scan window')}</dt><dd>${formatValue(radar.scan_timeout_seconds, ' s')} (${escapeHtml(radar.scan_mode || 'repeated')})</dd>
      <dt>${fieldLabel('lastScan', 'Last scan')}</dt><dd>${escapeHtml(radar.last_scan_started_at || '-')}</dd>
      <dt>${fieldLabel('nextRetry', 'Next retry')}</dt><dd>${escapeHtml(radar.next_retry_at || '-')}</dd>
      <dt>${fieldLabel('heart', 'Heart')}</dt><dd>${formatValue(latest.heartRate, ' BPM')}</dd>
      <dt>${fieldLabel('breath', 'Breath')}</dt><dd>${formatValue(latest.breathRate, ' /min')}</dd>
      <dt>${fieldLabel('presence', 'Presence')}</dt><dd>${formatBoolean(latest.present)}</dd>
      <dt>${fieldLabel('valid', 'Valid')}</dt><dd>${formatBoolean(latest.valid)}</dd>
      <dt>${fieldLabel('stabilized', 'Stabilized')}</dt><dd>${formatBoolean(latest.stabilized)}</dd>
      <dt>${fieldLabel('distance', 'Distance')}</dt><dd>${formatValue(latest.distance, ' cm')}</dd>
      <dt>${fieldLabel('phases', 'Phases')}</dt><dd>${formatPhases(latest)}</dd>
      <dt>${fieldLabel('sequence', 'Sequence')}</dt><dd>${escapeHtml(latest.sequence_number ?? '-')}</dd>
      <dt>${fieldLabel('drops', 'Drops')}</dt><dd>${formatDropInfo(latest)}</dd>
      <dt>${fieldLabel('timing', 'Timing')}</dt><dd>${formatTimingInfo(latest)}</dd>
      <dt>${fieldLabel('lastUpdateAge', 'Last update age')}</dt><dd>${formatValue(radar.seconds_since_last_activity, ' s')}</dd>
      <dt>${fieldLabel('message', 'Message')}</dt><dd>${escapeHtml(radar.last_message || '-')}</dd>
    </dl>
    ${renderRuntimeButtons(radar)}
  `;
}

function renderCameraEmotion(target, camera, worker, preview = {}, runtimeInfo = {}, sensorRuntime = {}) {
  if (!target) return;
  const latest = camera.latest || {};
  const liveLatest = preview.latest || {};
  const displayLatest = Object.keys(latest).length ? latest : liveLatest;
  const analysis = latest.analysis || liveLatest.analysis || {};
  target.innerHTML = `
    <div class="status-row">
      <span class="status-pill status-pill--${escapeHtml(camera.status || 'unknown')}">${escapeHtml(statusLabel(camera.status))}</span>
      <strong>${formatEnabled(camera.configured_enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${fieldLabel('mode', 'Mode')}</dt><dd>${escapeHtml(camera.worker_mode || '-')}</dd>
      <dt>${fieldLabel('interval', 'Interval')}</dt><dd>${formatValue(camera.snapshot_interval_ms, ' ms')}</dd>
      <dt>${fieldLabel('worker', 'Worker')}</dt><dd>${formatWorkerStatus(worker)}</dd>
      <dt>${fieldLabel('pythonPackages', 'Python packages')}</dt><dd>${formatRepairState(worker.dependency_install)}</dd>
      <dt>${fieldLabel('modelWeights', 'Model weights')}</dt><dd>${formatModelAssetState(worker)}</dd>
      <dt>${fieldLabel('workerWarmup', 'Worker warmup')}</dt><dd>${formatWorkerWarmup(worker)}</dd>
      <dt>${fieldLabel('emotion', 'Emotion')}</dt><dd>${escapeHtml(analysis.emotion || '-')}</dd>
      <dt>${fieldLabel('confidence', 'Confidence')}</dt><dd>${formatValue(analysis.confidence)}</dd>
      <dt>${fieldLabel('face', 'Face')}</dt><dd>${formatBoolean(analysis.face_detected)}</dd>
      <dt>${fieldLabel('frame', 'Frame')}</dt><dd>${formatFrame(displayLatest.frame)}</dd>
      <dt>${fieldLabel('processed', 'Processed')}</dt><dd>${escapeHtml(displayLatest.processed_at || '-')}</dd>
      <dt>${fieldLabel('message', 'Message')}</dt><dd>${escapeHtml(analysis.error || camera.last_message || worker.last_message || '-')}</dd>
    </dl>
    ${renderCameraLiveMonitor(preview, runtimeInfo, sensorRuntime)}
    ${renderEmotionWorkerInstall(worker)}
    ${renderRuntimeButtons(camera)}
  `;
}

function renderEmotionWorkerInstall(worker) {
  const installState = worker.dependency_install || {};
  const modelState = worker.model_asset_install || {};
  const running = installState.running === true || modelState.running === true;
  const status = modelState.status || installState.status || (running ? 'running' : '');
  const message = modelState.last_message || installState.last_message || t('dashboard.runtimeRepairHint', 'Repair DeepFace packages and model weights for the local Emotion Worker.');
  const output = [
    renderRepairOutput('dashboard.dependenciesOutput', 'Package output', installState.output_tail),
    renderRepairOutput('dashboard.modelWeightsOutput', 'Model weights output', modelState.output_tail),
  ].join('');
  return `
    <div class="dashboard-actions dashboard-actions--stacked">
      <button type="button" class="btn-secondary" data-dashboard-action="emotion_worker_repair_runtime"${running ? ' disabled' : ''}>
        ${escapeHtml(running ? t('dashboard.runtimeRepairRunning', 'Repairing...') : t('dashboard.runtimeRepair', 'Repair DeepFace runtime'))}
      </button>
      <span class="settings-hint">${escapeHtml(status ? `${status}: ${message}` : message)}</span>
      ${output}
    </div>`;
}

function renderRepairOutput(labelKey, fallback, output) {
  if (!output) return '';
  return `<details class="dependency-install-output"><summary>${escapeHtml(t(labelKey, fallback))}</summary><pre>${escapeHtml(output)}</pre></details>`;
}

function renderCameraLiveMonitor(preview, runtimeInfo, sensorRuntime = {}) {
  const latest = preview.latest || {};
  const analysis = latest.analysis || {};
  const image = preview.image || '';
  const secureWarning = runtimeInfo?.scheme === 'https'
    ? ''
    : `<span class="status-warning">${escapeHtml(t('dashboard.cameraLiveHttpsWarning', 'Tablet camera needs trusted HTTPS. Install and fully trust the Study Runner local Root CA on the iPad, then open the https:// tablet URL.'))}</span>`;
  const cameraEffective = sensorRuntime.effective?.camera_emotion === true;
  const message = cameraEffective
    ? (preview.last_message || t('dashboard.cameraLiveHint', 'Open the normal study page on the tablet. If camera emotion is enabled for the study, live frames appear here before recording starts.'))
    : t('dashboard.cameraLiveDisabled', 'Camera emotion is effectively disabled. Enable it in the dashboard or reset overrides to the study setting.');
  return `
    <div class="camera-live-monitor">
      <div class="camera-live-monitor-main">
        <div>
          <strong>${escapeHtml(t('dashboard.cameraLiveTitle', 'Tablet camera live monitor'))}</strong>
          <span class="status-muted">${escapeHtml(message)}</span>
          ${secureWarning}
        </div>
      </div>
      <div class="camera-live-monitor-body">
        <div class="camera-live-thumb">${image ? `<img src="${escapeHtml(image)}" alt="${escapeHtml(t('dashboard.cameraLiveImageAlt', 'Latest tablet camera live frame'))}">` : '-'}</div>
        <div class="camera-live-metrics">
          <span>${escapeHtml(t('dashboard.field.emotion', 'Emotion'))}: ${escapeHtml(analysis.emotion || '-')}</span>
          <span>${escapeHtml(t('dashboard.field.confidence', 'Confidence'))}: ${formatValue(analysis.confidence)}</span>
          <span>${escapeHtml(t('dashboard.field.face', 'Face'))}: ${formatBoolean(analysis.face_detected)}</span>
          <span>${escapeHtml(t('dashboard.field.frame', 'Frame'))}: ${formatFrame(latest.frame)}</span>
          <span>${escapeHtml(t('dashboard.field.processed', 'Processed'))}: ${escapeHtml(latest.processed_at || '-')}</span>
        </div>
      </div>
    </div>`;
}

function renderIntegrationControls(target, integrations, status = {}) {
  if (!target) return;
  const rows = Object.values(integrations).filter((item) => item && item.key);
  if (!rows.length) {
    target.innerHTML = `<p>${escapeHtml(t('dashboard.noIntegrations', 'No integrations registered.'))}</p>`;
    return;
  }
  const sensorRuntime = status.sensor_runtime || {};
  const hasOverrides = Object.values(sensorRuntime.override_active || {}).some(Boolean)
    || Object.keys(status.session_sensor_overrides || {}).length > 0;
  const warning = hasOverrides
    ? `<div class="integration-control-warning">${escapeHtml(t('dashboard.overrideWarning', 'Temporary dashboard overrides are active for this server session.'))}</div>`
    : '';
  const resetButton = hasOverrides
    ? `<button type="button" class="btn-secondary btn-xs" data-dashboard-action="reset_sensor_overrides">${escapeHtml(t('dashboard.overrideReset', 'Reset to study settings'))}</button>`
    : '';
  target.innerHTML = `<div class="integration-controls">
    ${warning}
    ${resetButton ? `<div class="integration-control-reset">${resetButton}</div>` : ''}
    ${rows.map((row) => renderIntegrationControlRow(row, sensorRuntime)).join('')}
  </div>`;
}

function renderIntegrationControlRow(item, sensorRuntime = {}) {
  const sensorEffective = sensorRuntime.effective || {};
  const hasSensorState = Object.prototype.hasOwnProperty.call(sensorEffective, item.key);
  const configured = hasSensorState ? Boolean(sensorEffective[item.key]) : Boolean(item.configured_enabled ?? item.enabled);
  const status = item.status || (configured ? 'enabled' : 'disabled');
  const toggleButtons = item.can_toggle ? `
    <button type="button" class="btn-secondary btn-xs" data-dashboard-action="toggle_${escapeHtml(item.key)}_on"${configured ? ' disabled' : ''}>${escapeHtml(t('dashboard.action.enable', 'Enable'))}</button>
    <button type="button" class="btn-secondary btn-xs" data-dashboard-action="toggle_${escapeHtml(item.key)}_off"${!configured ? ' disabled' : ''}>${escapeHtml(t('dashboard.action.disable', 'Disable'))}</button>
  ` : `<span class="integration-control-note">${escapeHtml(t('dashboard.managedByParent', 'Managed by parent integration'))}</span>`;
  const runtimeDetail = hasSensorState ? renderSensorRuntimeDetail(item.key, sensorRuntime) : '';

  return `
    <div class="integration-control-row">
      <div class="integration-control-main">
        <span class="status-pill status-pill--${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>
        <div>
          <strong>${escapeHtml(item.label || item.key)}</strong>
          <span>${buildIntegrationDetail(item)}</span>
          ${runtimeDetail}
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

function buildIntegrationDetail(item) {
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
  return `<span class="integration-runtime-detail">
    ${escapeHtml(t('dashboard.runtimeStudy', 'Study'))}: ${escapeHtml(formatOnOff(study[sensorKey]))}
    · ${escapeHtml(t('dashboard.runtimeOverride', 'Override'))}: ${escapeHtml(overrideLabel)}
    · ${escapeHtml(t('dashboard.runtimeEffective', 'Effective'))}: ${escapeHtml(formatOnOff(effective[sensorKey]))}
    ${hasOverride ? `<em>${escapeHtml(t('dashboard.temporaryOverride', 'temporary dashboard override'))}</em>` : ''}
  </span>`;
}

function renderXdf(target, status) {
  if (!target) return;
  const labrecorder = status.integrations?.labrecorder || {};
  const lsl = status.integrations?.lsl || {};
  target.innerHTML = `
    <dl class="status-list">
      <dt>${fieldLabel('primarySync', 'Primary sync')}</dt><dd>${escapeHtml(status.timestamp_strategy?.primary || 'LSL')}</dd>
      <dt>${fieldLabel('format', 'Format')}</dt><dd>${escapeHtml(status.timestamp_strategy?.recording_format || '.xdf')}</dd>
      <dt>${fieldLabel('lslMarkers', 'Study marker LSL', 'studyMarkerLsl')}</dt><dd>${formatEnabled(lsl.configured_enabled)}</dd>
      <dt>${fieldLabel('labRecorder', 'LabRecorder')}</dt><dd>${formatEnabled(labrecorder.configured_enabled)}</dd>
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

function formatCameraMonitorClient(client) {
  const parts = [
    `${escapeHtml(t('dashboard.requested', 'requested'))}: ${formatBoolean(client.camera_monitor_requested)}`,
    `${escapeHtml(t('dashboard.active', 'active'))}: ${formatBoolean(client.camera_monitor_active)}`,
  ];
  if (client.camera_last_error) {
    parts.push(`<span class="status-warning">${escapeHtml(client.camera_last_error)}</span>`);
  }
  return parts.join('<br>');
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

function formatQualityWithContact(quality, contactState) {
  const channels = formatSensorChannels(quality, ['O1', 'O2', 'T3', 'T4']);
  const contact = formatHealthValue(contactState);
  if (channels === '-') return contact === '-' ? '-' : contact;
  return `${channels}<br><span class="status-muted">${escapeHtml(t('dashboard.contactPrefix', 'contact'))}: ${contact}</span>`;
}

function renderBrainBitBand(brainbit) {
  const latest = brainbit.latest || {};
  const target = brainbit.target_device || latest.target_device || {};
  const selected = brainbit.selected_device || latest.selected_device || latest.device || {};
  const candidates = brainbit.scan_candidates || latest.scan_candidates || [];
  const lines = [];
  lines.push(`<div>${escapeHtml(t('dashboard.brainbitBandTarget', 'Target'))}: ${formatDeviceSummary(target) || escapeHtml(t('dashboard.noBandTarget', 'no target set'))}</div>`);
  lines.push(`<div>${escapeHtml(t('dashboard.brainbitBandSelected', 'Connected'))}: ${formatDeviceSummary(selected) || '-'}</div>`);
  if (Array.isArray(candidates) && candidates.length) {
    lines.push(`<div class="brainbit-band-list">${candidates.map(renderBrainBitCandidate).join('')}</div>`);
  }
  return lines.join('');
}

function renderBrainBitCandidate(candidate) {
  const summary = formatDeviceSummary(candidate) || escapeHtml(t('dashboard.unknown', 'unknown'));
  const rssi = candidate.rssi === null || candidate.rssi === undefined ? '' : ` RSSI ${candidate.rssi}`;
  return `<div class="brainbit-band-row">
    <span class="brainbit-band-meta">#${escapeHtml(candidate.index ?? '-')}: ${summary}${escapeHtml(rssi)}</span>
    <button type="button" class="btn-secondary btn-xs" data-dashboard-action="brainbit_select" data-device-index="${dataAttr(candidate.index)}" data-device-name="${dataAttr(candidate.name)}" data-device-address="${dataAttr(candidate.address)}" data-serial-number="${dataAttr(candidate.serial || candidate.serial_number)}">${escapeHtml(t('dashboard.useBand', 'Use this band'))}</button>
  </div>`;
}

function formatDeviceSummary(device) {
  if (!device || typeof device !== 'object' || !Object.keys(device).length) return '';
  const parts = [];
  if (device.name) parts.push(device.name);
  if (device.serial || device.serial_number) parts.push(`serial ${device.serial || device.serial_number}`);
  if (device.address) parts.push(device.address);
  if (device.family) parts.push(device.family);
  return escapeHtml(parts.filter(Boolean).join(' - '));
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

function formatWorkerStatus(worker) {
  const status = escapeHtml(worker.status ? statusLabel(worker.status) : '-');
  if (!worker.last_message) return status;
  return `${status}<br><span class="status-muted">${escapeHtml(worker.last_message)}</span>`;
}

function formatRepairState(state) {
  if (!state || typeof state !== 'object') return '-';
  const status = state.status || (state.running ? 'running' : '');
  const message = state.last_message || '';
  if (!status && !message) return '-';
  return `${escapeHtml(status ? statusLabel(status) : '-')}${message ? `<br><span class="status-muted">${escapeHtml(message)}</span>` : ''}`;
}

function formatModelAssetState(worker) {
  const state = worker.model_asset_install || {};
  const assetName = worker.model_asset_name || state.asset_name || '';
  const assetPath = worker.model_asset_path || state.asset_path || '';
  const stateText = formatRepairState(state);
  const parts = [];
  if (stateText !== '-') parts.push(stateText);
  if (assetName) parts.push(`<span class="status-muted">${escapeHtml(assetName)}</span>`);
  if (assetPath) parts.push(`<span class="status-muted">${escapeHtml(assetPath)}</span>`);
  return parts.length ? parts.join('<br>') : '-';
}

function formatWorkerWarmup(worker) {
  const latest = worker.latest || {};
  if (latest.model_ready === true) return escapeHtml(t('dashboard.health.ready', 'ready'));
  if (latest.model_error_class || worker.model_error_class) {
    const errorClass = latest.model_error_class || worker.model_error_class;
    const action = latest.suggested_action || worker.suggested_action || '';
    const detail = latest.model_error || '';
    return `${escapeHtml(errorClass)}${action ? `<br><span class="status-muted">${escapeHtml(action)}</span>` : ''}${detail ? `<br><span class="status-muted">${escapeHtml(detail)}</span>` : ''}`;
  }
  if (latest.model_checked === true) return escapeHtml(t('dashboard.health.waiting', 'waiting'));
  return '-';
}

function formatIntegrationName(key) {
  const names = {
    brainbit: 'BrainBit',
    mini_radar: 'MR60 BLE',
    camera_emotion: 'Camera emotion',
    emotion_worker: 'Emotion Worker',
    lsl: 'LSL',
    labrecorder: 'LabRecorder',
    osc: 'OSC',
    notion: 'Notion',
  };
  return names[key] || key;
}

function dataAttr(value) {
  return escapeHtml(value === null || value === undefined ? '' : value);
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
