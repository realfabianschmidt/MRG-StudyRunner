/** Optional trusted dashboard renderer for the BrainBit plugin. */
export function renderDashboard({ integration: brainbit }, ui) {
  const latest = brainbit.latest || {};
  const battery = latest.battery || {};
  const quality = latest.quality || {};
  const bands = latest.bands || {};
  const mental = latest.mental || {};
  const calibration = latest.calibration || {};
  const contactState = brainbit.contact_quality_state
    || latest.contact_quality_state
    || brainbit.health?.contact
    || 'unknown';

  return `
    <div class="status-row">
      <span class="status-pill status-pill--${ui.escapeHtml(brainbit.status || 'unknown')}">${ui.escapeHtml(ui.statusLabel(brainbit.status))}</span>
      <strong>${ui.formatEnabled(brainbit.configured_enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${ui.fieldLabel('scanWindow', 'Scan window')}</dt><dd>${ui.formatValue(brainbit.scan_timeout_seconds, ' s')} (${ui.escapeHtml(brainbit.scan_mode || 'one-shot')})</dd>
      <dt>${ui.fieldLabel('lastScan', 'Last scan')}</dt><dd>${ui.escapeHtml(brainbit.last_scan_started_at || '-')}</dd>
      <dt>${ui.fieldLabel('band', 'Band')}</dt><dd>${renderBand(brainbit, ui)}</dd>
      <dt>${ui.fieldLabel('battery', 'Battery')}</dt><dd>${ui.formatValue(battery.percent, '%')}</dd>
      <dt>${ui.fieldLabel('quality', 'Quality')}</dt><dd>${formatQuality(quality, contactState, ui)}</dd>
      <dt>${ui.fieldLabel('bands', 'Bands')}</dt><dd>${ui.formatSensorChannels(bands, ['delta', 'theta', 'alpha', 'beta', 'gamma'])}</dd>
      <dt>${ui.fieldLabel('mental', 'Mental')}</dt><dd>${ui.formatSensorChannels(mental, ['Inst_Attention', 'Inst_Relaxation', 'Rel_Attention', 'Rel_Relaxation'])}</dd>
      <dt>${ui.fieldLabel('calibration', 'Calibration')}</dt><dd>${formatCalibration(calibration, ui)}</dd>
      <dt>${ui.fieldLabel('brainbitDataLsl', 'BrainBit data LSL')}</dt><dd>${ui.formatEnabled(brainbit.lsl_enabled)}</dd>
      <dt>${ui.fieldLabel('touchdesigner', 'TouchDesigner')}</dt><dd>${ui.formatEnabled(brainbit.touchdesigner_forwarding_enabled)}</dd>
      <dt>${ui.fieldLabel('lastActive', 'Last active')}</dt><dd>${ui.formatTimestampAge(latest.last_activity_at || brainbit.last_activity_at, brainbit.seconds_since_last_activity)}</dd>
      <dt>${ui.fieldLabel('message', 'Message')}</dt><dd>${formatMessage(brainbit, latest, ui)}</dd>
    </dl>
    ${ui.renderRuntimeButtons(brainbit)}
  `;
}

function formatMessage(brainbit, latest, ui) {
  const fallback = latest.last_message || brainbit.last_message || '-';
  const detailKey = latest.status_detail_key || brainbit.status_detail_key;
  const hintKey = latest.status_detail_hint_key || brainbit.status_detail_hint_key;
  const message = detailKey ? ui.t(detailKey, fallback) : fallback;
  const hint = hintKey ? ui.t(hintKey, '') : '';
  return hint
    ? `${ui.escapeHtml(message)}<br><span class="status-muted">${ui.escapeHtml(hint)}</span>`
    : ui.escapeHtml(message);
}

function formatQuality(quality, contactState, ui) {
  const channels = ui.formatSensorChannels(quality, ['O1', 'O2', 'T3', 'T4']);
  const contact = ui.formatHealthValue(contactState);
  if (channels === '-') return contact === '-' ? '-' : contact;
  return `${channels}<br><span class="status-muted">${ui.escapeHtml(ui.t('dashboard.contactPrefix', 'contact'))}: ${contact}</span>`;
}

function renderBand(brainbit, ui) {
  const latest = brainbit.latest || {};
  const target = brainbit.target_device || latest.target_device || {};
  const selected = brainbit.selected_device || latest.selected_device || latest.device || {};
  return [
    `<div>${ui.escapeHtml(ui.t('dashboard.brainbitBandTarget', 'Target'))}: ${formatDevice(target, ui) || ui.escapeHtml(ui.t('dashboard.noBandTarget', 'no target set'))}</div>`,
    `<div>${ui.escapeHtml(ui.t('dashboard.brainbitBandSelected', 'Connected'))}: ${formatDevice(selected, ui) || '-'}</div>`,
  ].join('');
}

function formatDevice(device, ui) {
  if (!device || typeof device !== 'object' || !Object.keys(device).length) return '';
  const parts = [];
  if (device.name) parts.push(device.name);
  if (device.serial || device.serial_number) parts.push(`serial ${device.serial || device.serial_number}`);
  if (device.address) parts.push(device.address);
  if (device.family) parts.push(device.family);
  return ui.escapeHtml(parts.filter(Boolean).join(' - '));
}

function formatCalibration(calibration, ui) {
  if (!calibration || typeof calibration !== 'object' || !Object.keys(calibration).length) return '-';
  const parts = [];
  if (calibration.event) parts.push(ui.escapeHtml(calibration.event));
  if (calibration.progress_percent !== null && calibration.progress_percent !== undefined) {
    parts.push(ui.formatValue(calibration.progress_percent, '%'));
  }
  if (calibration.stage) parts.push(`stage ${ui.escapeHtml(calibration.stage)}`);
  return parts.length ? parts.join(', ') : ui.formatObjectBrief(calibration);
}
