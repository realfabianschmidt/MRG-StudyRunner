/** Optional trusted dashboard renderer for the BrainBit plugin. */
/**
 * Mental-index series get a hover explanation (Instant vs. Relative isn't
 * self-evident); band-power series (delta/theta/...) don't need one.
 */
const MENTAL_HELP_KEYS = {
  Inst_Attention: ['brainbitMentalInstAttention', "Instant attention: the SDK attention index for the current analysis window. This preview updates at most once per second."],
  Inst_Relaxation: ['brainbitMentalInstRelaxation', "Instant relaxation: the SDK relaxation index for the current analysis window. This preview updates at most once per second."],
  Rel_Attention: ['brainbitMentalRelAttention', "Relative attention: the SDK attention index relative to calibration. An algorithmic index, not a direct measurement of attention."],
  Rel_Relaxation: ['brainbitMentalRelRelaxation', "Relative relaxation: the SDK relaxation index relative to calibration. An algorithmic index, not a direct measurement of relaxation."],
};

// Scale against the whole visible window so older peaks remain accurate.
const HEADROOM_FACTOR = 1.15;
const MIN_DISPLAY_MAX = 0.05;

function computeDisplayMax(points, end, names) {
  let recentMax = 0;
  for (const point of points) {
    if (point.at < end - 60 || point.at > end || point.validity !== 'valid') continue;
    for (const name of names) {
      const value = point.values?.[name];
      if (Number.isFinite(value) && value > recentMax) recentMax = value;
    }
  }
  return Math.max(MIN_DISPLAY_MAX, Math.ceil(recentMax * HEADROOM_FACTOR / MIN_DISPLAY_MAX) * MIN_DISPLAY_MAX);
}

export function renderTrend(kind, plugin, ui, now = Date.now() / 1000) {
  const names = kind === 'bands' ? ['delta', 'theta', 'alpha', 'beta', 'gamma']
    : ['Inst_Attention', 'Inst_Relaxation', 'Rel_Attention', 'Rel_Relaxation'];
  const colors = ['#2166ac', '#b2182b', '#008060', '#7950a3', '#936000'];
  const points = (plugin.preview?.[kind] || []).filter((p) => p.connection_id === plugin.connection_id);
  const last = points.at(-1);
  const end = last ? last.at + Math.max(0, now - last.received_at) : now;
  const title = ui.t(`brainbit.monitor.${kind}`, kind === 'bands' ? 'Band power' : 'SDK attention / relaxation indices');
  const displayMax = computeDisplayMax(points, end, names);
  const paths = names.map((name, index) => {
    let d = '', previous = null;
    for (const point of points) {
      const value = point.values?.[name];
      if (point.at < end - 60 || point.at > end || point.validity !== 'valid' || !Number.isFinite(value) || value < 0) { previous = null; continue; }
      const x = 32 + (point.at - (end - 60)) / 60 * 306;
      const y = 110 - value / displayMax * 92;
      d += `${previous !== null && point.at - previous < 1.6 ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `;
      previous = point.at;
    }
    return `<path d="${d}" fill="none" stroke="${colors[index]}" stroke-width="2" stroke-dasharray="${index % 2 ? '5 2' : 'none'}" />`;
  }).join('');
  const legend = names.map((name, index) => {
    const label = ui.escapeHtml(ui.t(`brainbit.channel.${name}`, name));
    const help = MENTAL_HELP_KEYS[name];
    const span = help
      ? `<span style="color:${colors[index]}" class="status-label-help" tabindex="0" title="${ui.escapeHtml(ui.t(`dashboard.help.${help[0]}`, help[1]))}">${label}</span>`
      : `<span style="color:${colors[index]}">${label}</span>`;
    return span;
  }).join(' · ');
  return `<section aria-label="${ui.escapeHtml(title)}"><strong>${ui.escapeHtml(title)}</strong>
    <svg viewBox="0 0 360 140" width="100%" role="img" aria-label="${ui.escapeHtml(title)}">
      <path d="M32 18V110H338" fill="none" stroke="currentColor" opacity=".4" />
      <g fill="currentColor" font-size="10"><text x="2" y="22">${Math.round(displayMax * 100)}%</text><text x="12" y="114">0%</text>
      <text x="32" y="130">−60 s</text><text x="318" y="130">0 s</text></g>${paths}</svg>
    <small>${legend}<br>${ui.escapeHtml(ui.t('brainbit.monitor.previewNote', "60-second preview, at most 1 Hz. The scale follows the visible values; gaps indicate unavailable or uncertain data."))}
    ${last ? ` · ${ui.escapeHtml(ui.t('brainbit.monitor.age', 'Age'))}: ${Math.max(0, now - last.received_at).toFixed(0)} s` : ''}</small></section>`;
}

/**
 * Connection, electrode contact, calibration, the switch and the guided
 * buttons are drawn by the shared connection panel for every sensor
 * (apps/ui/scripts/admin/sensor-connection-panel.js). This tile adds only
 * what is specific to BrainBit: notes, the two previews and the details.
 */
export function renderDashboard({ plugin: brainbit, manifest }, ui) {
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
  const notes = formatMessage(brainbit, latest, ui);

  return `
    ${notes ? `<p class="status-muted" role="status">${notes}</p>` : ''}
    ${renderTrend('bands', brainbit, ui)}
    ${renderTrend('mental', brainbit, ui)}
    <details><summary>${ui.escapeHtml(ui.t('brainbit.monitor.details', 'Acquisition details'))}</summary>
    <dl class="status-list">
      <dt>${ui.fieldLabel('scanWindow', 'Scan window')}</dt><dd>${ui.formatValue(brainbit.scan_timeout_seconds, ' s')}</dd>
      <dt>${ui.fieldLabel('lastScan', 'Last scan')}</dt><dd>${ui.escapeHtml(brainbit.last_scan_started_at || '-')}</dd>
      ${renderRetryRow(brainbit, ui)}
      <dt>${ui.fieldLabel('band', 'Band')}</dt><dd>${renderBand(brainbit, ui)}</dd>
      <dt>${ui.fieldLabel('channels', 'Channels')}</dt><dd>${channels.length ? ui.escapeHtml(channels.join(', ')) : '-'}</dd>
      <dt>${ui.fieldLabel('battery', 'Battery')}</dt><dd>${ui.formatValue(battery.percent, '%')} ${brainbit.battery_stale ? ui.escapeHtml(ui.t('brainbit.monitor.outdated', 'outdated / unknown')) : ''}</dd>
      <dt>${ui.fieldLabel('rawEeg', 'Latest raw EEG')}</dt><dd>${formatExactChannels(eeg, channels, ui, latest.eeg_batch?.units || '')}</dd>
      <dt>${ui.fieldLabel('resistance', 'Resistance')}</dt><dd>${formatExactChannels(resistance, channels, ui, resistance.units || 'Ohm')}</dd>
      <dt>${ui.fieldLabel('quality', 'Quality')}</dt><dd>${formatQuality(quality, channels, contactState, brainbit.contact_quality_as_of || latest.contact_quality_as_of, ui)}</dd>
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
      <dt>${ui.escapeHtml(ui.t('brainbit.monitor.lastEvent', 'Last connection event'))}</dt><dd>${ui.escapeHtml(brainbit.last_event?.tag || '-')} ${brainbit.last_event?.at ? ui.escapeHtml(new Date(brainbit.last_event.at * 1000).toLocaleString()) : ''}</dd>
      <dt>${ui.escapeHtml(ui.t('brainbit.monitor.artifacts', 'Live artifacts'))}</dt><dd>${ui.escapeHtml(ui.t(`brainbit.monitor.${latest.artifact?.both_now || latest.artifact?.sequence ? 'detected' : latest.artifact ? 'notDetected' : 'unknown'}`, 'unknown'))}</dd>
      <dt>${ui.escapeHtml(ui.t('brainbit.monitor.artifactFraction', 'Artifact time since connection'))}</dt><dd>${ui.formatValue(Number.isFinite(brainbit.artifact_fraction) ? brainbit.artifact_fraction * 100 : null, '%')}</dd>
    </dl>
    </details>
  `;
}

/**
 * Shown only while a reconnection is pending.
 *
 * Without it, a band that is switched off looks like a stuck plugin, and the
 * natural reaction is to press Restart -- which aborts the attempt that was
 * already on its way.
 */
function renderRetryRow(brainbit, ui) {
  const nextRetryAt = brainbit.next_retry_at || brainbit.latest?.next_retry_at;
  if (!nextRetryAt) return '';
  const attempt = brainbit.retry_attempt || brainbit.latest?.retry_attempt;
  const suffix = attempt ? ` (${ui.escapeHtml(ui.t('dashboard.attempt', 'attempt'))} ${ui.escapeHtml(String(attempt))})` : '';
  return `<dt>${ui.fieldLabel('nextAttempt', 'Next attempt')}</dt><dd>${ui.escapeHtml(nextRetryAt)}${suffix}</dd>`;
}

/** Notes the shared connection panel does not show: battery, artifacts, failures. */
function formatMessage(brainbit, latest, ui) {
  const parts = [];
  if (brainbit.connection_state === 'connected') {
    if (brainbit.low_battery) parts.push(ui.t('brainbit.monitor.lowBattery', 'low battery'));
    if (latest.artifact?.both_now || latest.artifact?.sequence) parts.push(ui.t('brainbit.monitor.artifacts', 'Live artifacts'));
    if (latest.derived_error) parts.push(ui.t('brainbit.monitor.derivedUnavailable', 'Derived metrics unavailable; raw EEG is independent'));
    return parts.map((part) => ui.escapeHtml(part)).join(' · ');
  }
  if (brainbit.connection_state === 'failed' || (!brainbit.connection_state && (latest.status_detail_key || brainbit.status_detail_key))) {
    const fallback = latest.last_message || brainbit.last_message || '';
    const detailKey = latest.status_detail_key || brainbit.status_detail_key;
    const hintKey = latest.status_detail_hint_key || brainbit.status_detail_hint_key;
    const message = detailKey ? ui.t(detailKey, fallback) : fallback;
    const hint = hintKey ? ui.t(hintKey, '') : '';
    return hint
      ? `${ui.escapeHtml(message)}<br><span class="status-muted">${ui.escapeHtml(hint)}</span>`
      : ui.escapeHtml(message);
  }
  return '';
}

function formatQuality(quality, channelNames, contactState, measuredAt, ui) {
  const channels = formatExactChannels(quality, channelNames, ui, quality.units || 'ratio');
  const contact = ui.formatHealthValue(contactState);
  // Contact is measured once before streaming and never refreshed, so the time
  // it was taken belongs next to it -- otherwise a reading from the start of a
  // long session reads as if it were live.
  const measured = measuredAt ? ` · ${ui.escapeHtml(ui.t('dashboard.measuredAt', 'measured'))} ${ui.escapeHtml(measuredAt)}` : '';
  if (channels === '-') return contact === '-' ? '-' : `${contact}${measured}`;
  return `${channels}<br><span class="status-muted">${ui.escapeHtml(ui.t('brainbit.monitor.contactDiagnostic', 'Diagnostic conversion, not a validated quality percentage'))}<br>${ui.escapeHtml(ui.t('dashboard.contactPrefix', 'contact'))}: ${contact}${measured}</span>`;
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
    // The nominal rate was all that was ever shown, so a band delivering half
    // its samples looked perfectly healthy. This is what it is really sending.
    `measured rate: ${batch.measured_hz ?? latest.measured_sample_rate_hz ?? '-'} Hz`,
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
    `<div>${ui.escapeHtml(ui.t('brainbit.monitor.selected', 'Selected device'))}: ${formatDevice(selected, ui) || '-'}</div>`,
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
