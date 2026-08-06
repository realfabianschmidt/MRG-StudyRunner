/** Optional trusted dashboard renderer for the camera/emotion plugin. */
export function renderDashboard({ integration: camera, status }, ui) {
  const preview = camera.preview || {};
  const runtimeInfo = status.runtime_info || {};
  const sensorRuntime = status.sensor_runtime || {};
  const latest = camera.latest || {};
  const liveLatest = preview.latest || {};
  const displayLatest = Object.keys(latest).length ? latest : liveLatest;
  const analysis = latest.analysis || liveLatest.analysis || {};
  return `
    <div class="status-row">
      <span class="status-pill status-pill--${ui.escapeHtml(camera.status || 'unknown')}">${ui.escapeHtml(ui.statusLabel(camera.status))}</span>
      <strong>${ui.formatEnabled(camera.configured_enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${ui.fieldLabel('mode', 'Mode')}</dt><dd>${ui.escapeHtml(camera.worker_mode || '-')}</dd>
      <dt>${ui.fieldLabel('interval', 'Interval')}</dt><dd>${ui.formatValue(camera.snapshot_interval_ms, ' ms')}</dd>
      <dt>${ui.fieldLabel('emotion', 'Emotion')}</dt><dd>${ui.escapeHtml(analysis.emotion || '-')}</dd>
      <dt>${ui.fieldLabel('confidence', 'Confidence')}</dt><dd>${ui.formatValue(analysis.confidence)}</dd>
      <dt>${ui.fieldLabel('face', 'Face')}</dt><dd>${ui.formatBoolean(analysis.face_detected)}</dd>
      <dt>${ui.fieldLabel('frame', 'Frame')}</dt><dd>${formatFrame(displayLatest.frame, ui)}</dd>
      <dt>${ui.fieldLabel('processed', 'Processed')}</dt><dd>${ui.escapeHtml(displayLatest.processed_at || '-')}</dd>
      <dt>${ui.fieldLabel('message', 'Message')}</dt><dd>${ui.escapeHtml(analysis.error || camera.last_message || '-')}</dd>
    </dl>
    ${renderLiveMonitor(preview, runtimeInfo, sensorRuntime, ui)}
    ${ui.renderRuntimeButtons(camera)}
  `;
}

function renderLiveMonitor(preview, runtimeInfo, sensorRuntime, ui) {
  const latest = preview.latest || {};
  const analysis = latest.analysis || {};
  const image = preview.image || '';
  const secureWarning = runtimeInfo?.scheme === 'https'
    ? ''
    : `<span class="status-warning">${ui.escapeHtml(ui.t('dashboard.cameraLiveHttpsWarning', 'Tablet camera needs trusted HTTPS. Install and fully trust the Study Runner local Root CA on the iPad, then open the https:// tablet URL.'))}</span>`;
  const enabled = sensorRuntime.effective?.camera_emotion === true;
  const message = enabled
    ? (preview.last_message || ui.t('dashboard.cameraLiveHint', 'Open the normal study page on the tablet. If camera emotion is enabled for the study, live frames appear here before recording starts.'))
    : ui.t('dashboard.cameraLiveDisabled', 'Camera emotion is effectively disabled. Enable it in the dashboard or reset overrides to the study setting.');
  return `
    <div class="camera-live-monitor">
      <div class="camera-live-monitor-main"><div>
        <strong>${ui.escapeHtml(ui.t('dashboard.cameraLiveTitle', 'Tablet camera live monitor'))}</strong>
        <span class="status-muted">${ui.escapeHtml(message)}</span>${secureWarning}
      </div></div>
      <div class="camera-live-monitor-body">
        <div class="camera-live-thumb">${image ? `<img src="${ui.escapeHtml(image)}" alt="${ui.escapeHtml(ui.t('dashboard.cameraLiveImageAlt', 'Latest tablet camera live frame'))}">` : '-'}</div>
        <div class="camera-live-metrics">
          <span>${ui.escapeHtml(ui.t('dashboard.field.emotion', 'Emotion'))}: ${ui.escapeHtml(analysis.emotion || '-')}</span>
          <span>${ui.escapeHtml(ui.t('dashboard.field.confidence', 'Confidence'))}: ${ui.formatValue(analysis.confidence)}</span>
          <span>${ui.escapeHtml(ui.t('dashboard.field.face', 'Face'))}: ${ui.formatBoolean(analysis.face_detected)}</span>
          <span>${ui.escapeHtml(ui.t('dashboard.field.frame', 'Frame'))}: ${formatFrame(latest.frame, ui)}</span>
          <span>${ui.escapeHtml(ui.t('dashboard.field.processed', 'Processed'))}: ${ui.escapeHtml(latest.processed_at || '-')}</span>
        </div>
      </div>
    </div>`;
}

function formatFrame(frame, ui) {
  if (!frame) return '-';
  return `${ui.escapeHtml(frame.width || '-')} x ${ui.escapeHtml(frame.height || '-')}`;
}
