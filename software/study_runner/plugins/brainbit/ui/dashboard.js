/** Optional trusted dashboard renderer for the BrainBit plugin. */
export function renderDashboard({ plugin: brainbit }, ui) {
  const latest = brainbit.latest || {};
  const battery = latest.battery || {};
  const quality = latest.quality || {};
  const resistance = latest.resist || {};
  const eeg = latest.eeg || {};
  const bands = latest.bands || {};
  const mental = latest.mental || {};
  const calibration = latest.calibration || {};
  const channels = channelLabels(brainbit, latest);
  const contactState = brainbit.contact_quality_state
    || latest.contact_quality_state
    || brainbit.health?.contact
    || 'unknown';

  return `
    <div class="status-row">
      <span class="status-pill status-pill--${ui.escapeHtml(brainbit.status || 'unknown')}">${ui.escapeHtml(ui.statusLabel(brainbit.status))}</span>
      <strong>${ui.formatEnabled(brainbit.configured_enabled ?? brainbit.enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${ui.fieldLabel('scanWindow', 'Scan window')}</dt><dd>${ui.formatValue(brainbit.scan_timeout_seconds, ' s')} (${ui.escapeHtml(brainbit.scan_mode || 'one-shot')})</dd>
      <dt>${ui.fieldLabel('lastScan', 'Last scan')}</dt><dd>${ui.escapeHtml(brainbit.last_scan_started_at || '-')}</dd>
      <dt>${ui.fieldLabel('band', 'Band')}</dt><dd>${renderBand(brainbit, ui)}</dd>
      <dt>${ui.fieldLabel('channels', 'Channels')}</dt><dd>${channels.length ? ui.escapeHtml(channels.join(', ')) : '-'}</dd>
      <dt>${ui.fieldLabel('battery', 'Battery')}</dt><dd>${ui.formatValue(battery.percent, '%')}</dd>
      <dt>${ui.fieldLabel('rawEeg', 'Latest raw EEG')}</dt><dd>${formatExactChannels(eeg, channels, ui, latest.eeg_batch?.units || '')}</dd>
      <dt>${ui.fieldLabel('resistance', 'Resistance')}</dt><dd>${formatExactChannels(resistance, channels, ui, resistance.units || 'Ohm')}</dd>
      <dt>${ui.fieldLabel('quality', 'Quality')}</dt><dd>${formatQuality(quality, channels, contactState, ui)}</dd>
      <dt>${ui.fieldLabel('bands', 'Bands')}</dt><dd>${ui.formatSensorChannels(bands, ['delta', 'theta', 'alpha', 'beta', 'gamma'])}</dd>
      <dt>${ui.fieldLabel('mental', 'Mental')}</dt><dd>${ui.formatSensorChannels(mental, ['Inst_Attention', 'Inst_Relaxation', 'Rel_Attention', 'Rel_Relaxation'])}</dd>
      <dt>${ui.fieldLabel('calibration', 'Calibration')}</dt><dd>${formatCalibration(calibration, ui)}</dd>
      <dt>${ui.fieldLabel('health', 'Acquisition health')}</dt><dd>${formatHealth(brainbit.health || {}, ui)}</dd>
      <dt>${ui.fieldLabel('integrity', 'Packet integrity')}</dt><dd>${formatIntegrity(latest, ui)}</dd>
      <dt>${ui.fieldLabel('streams', 'Actual streams')}</dt><dd>${formatStreams(brainbit.actual_streams || latest.actual_streams, ui)}</dd>
      <dt>${ui.fieldLabel('brainbitDataLsl', 'BrainBit data LSL')}</dt><dd>${ui.formatEnabled(brainbit.lsl_enabled)}</dd>
      <dt>${ui.fieldLabel('touchdesigner', 'TouchDesigner')}</dt><dd>${ui.formatEnabled(brainbit.touchdesigner_forwarding_enabled)}</dd>
      <dt>${ui.fieldLabel('lastActive', 'Last active')}</dt><dd>${ui.formatTimestampAge(latest.last_activity_at || brainbit.last_activity_at, brainbit.seconds_since_last_activity)}</dd>
      <dt>${ui.fieldLabel('diagnostics', 'Diagnostics')}</dt><dd>${formatDiagnostics(brainbit, latest, ui)}</dd>
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

function formatQuality(quality, channelNames, contactState, ui) {
  const channels = formatExactChannels(quality, channelNames, ui, quality.units || 'ratio');
  const contact = ui.formatHealthValue(contactState);
  if (channels === '-') return contact === '-' ? '-' : contact;
  return `${channels}<br><span class="status-muted">${ui.escapeHtml(ui.t('dashboard.contactPrefix', 'contact'))}: ${contact}</span>`;
}

function channelLabels(brainbit, latest) {
  const direct = brainbit.supported_channels || latest.supported_channels;
  if (Array.isArray(direct) && direct.length) return uniqueLabels(direct);
  const streams = brainbit.actual_streams || latest.actual_streams || [];
  const eegStream = Array.isArray(streams) ? streams.find((stream) => stream?.key === 'eeg') : null;
  if (Array.isArray(eegStream?.channels) && eegStream.channels.length) return uniqueLabels(eegStream.channels);
  const batchChannels = latest.eeg_batch?.channels;
  if (Array.isArray(batchChannels) && batchChannels.length) return uniqueLabels(batchChannels);
  for (const source of [latest.eeg, latest.resist, latest.quality]) {
    if (source && typeof source === 'object') {
      const labels = Object.keys(source).filter((key) => !CHANNEL_METADATA.has(key));
      if (labels.length) return uniqueLabels(labels);
    }
  }
  return [];
}

function uniqueLabels(values) {
  return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))];
}

const CHANNEL_METADATA = new Set([
  'ts', 'pack', 'marker', 'units', 'source_units', 'processing', 'packet_shape',
  'open_channels', 'referents_ohm', 'resistance_upper_ohm', 'quality_model',
]);

function formatExactChannels(source, channels, ui, unit = '') {
  if (!source || typeof source !== 'object') return '-';
  const labels = channels.length
    ? channels
    : Object.keys(source).filter((key) => !CHANNEL_METADATA.has(key));
  const rows = labels
    .filter((label) => source[label] !== undefined)
    .map((label) => {
      const value = source[label];
      const rendered = value === null ? 'null' : String(value);
      return `${ui.escapeHtml(label)}: ${ui.escapeHtml(rendered)}${unit ? ` ${ui.escapeHtml(unit)}` : ''}`;
    });
  return rows.length ? rows.join('<br>') : '-';
}

function formatHealth(health, ui) {
  const keys = ['process', 'connection', 'raw_eeg', 'derived_metrics', 'data_integrity', 'recording', 'log_output'];
  const rows = keys
    .filter((key) => health[key] !== undefined)
    .map((key) => `${ui.escapeHtml(key.replaceAll('_', ' '))}: ${ui.formatHealthValue(health[key])}`);
  return rows.length ? rows.join('<br>') : '-';
}

function formatIntegrity(latest, ui) {
  const batch = latest.eeg_batch || {};
  const warning = latest.data_warning || {};
  const rows = [
    `batch samples: ${batch.sample_count ?? '-'}`,
    `last packet: ${batch.last_pack ?? '-'}`,
    `gap frames (batch / total): ${batch.packet_gap_frames ?? 0} / ${batch.packet_gap_frames_total ?? warning.packet_gap_frames_total ?? 0}`,
    `counter resets: ${batch.packet_counter_reset_total ?? warning.packet_counter_reset_total ?? 0}`,
    `warnings: ${latest.data_warning_count ?? 0}`,
  ];
  return rows.map((row) => ui.escapeHtml(row)).join('<br>');
}

function formatStreams(streams, ui) {
  if (!Array.isArray(streams) || !streams.length) return '-';
  return streams.map((stream) => {
    const rate = Number(stream?.nominal_rate_hz);
    const rateLabel = Number.isFinite(rate) && rate > 0 ? `${rate} Hz` : 'irregular';
    const channels = Array.isArray(stream?.channels) ? stream.channels.join(', ') : '-';
    return ui.escapeHtml(`${stream?.key || stream?.type || 'stream'} · ${rateLabel} · ${channels}`);
  }).join('<br>');
}

function formatDiagnostics(brainbit, latest, ui) {
  const entries = [
    ['callback', latest.callback_error],
    ['stream', latest.stream_error],
    ['LSL', latest.lsl_error || brainbit.lsl_error],
    ['log', latest.log_error],
    ['data', latest.data_warning],
  ].filter(([, value]) => value);
  if (!entries.length) return ui.escapeHtml(`log: ${brainbit.raw_log_path || '-'}`);
  return entries.map(([label, value]) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value);
    return `${ui.escapeHtml(label)}: ${ui.escapeHtml(text)}`;
  }).join('<br>');
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
