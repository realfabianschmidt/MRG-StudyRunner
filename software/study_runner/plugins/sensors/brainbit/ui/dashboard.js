/** Optional trusted dashboard renderer for the BrainBit plugin. */
function connectionLabel(plugin, ui) {
  const state = plugin.connection_state || 'unknown';
  return ui.t(`brainbit.connection.${state}`, state.replaceAll('_', ' '));
}

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

  return `
    ${renderDeviceToolbar(brainbit, manifest, ui)}
    <div class="dashboard-status-row">
      <div class="dashboard-status-row-info">
        <span class="status-pill status-pill--${ui.escapeHtml(brainbit.status || 'unknown')}">${ui.escapeHtml(connectionLabel(brainbit, ui))}</span>
        <strong>${ui.formatEnabled(brainbit.configured_enabled ?? brainbit.enabled)}</strong>
      </div>
      ${renderRuntimeToggle(brainbit, ui)}
    </div>
    <p role="status">${formatMessage(brainbit, latest, ui)}</p>
    ${renderTrend('bands', brainbit, ui)}
    ${renderTrend('mental', brainbit, ui)}
    <details><summary>${ui.escapeHtml(ui.t('brainbit.monitor.details', 'Acquisition details'))}</summary>
    <dl class="status-list">
      <dt>${ui.fieldLabel('scanWindow', 'Scan window')}</dt><dd>${ui.formatValue(brainbit.scan_timeout_seconds, ' s')} (${ui.escapeHtml(brainbit.scan_mode || 'one-shot')})</dd>
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

/** Device select + search/retest-contact/connect, as icon actions, above the fold. */
function renderDeviceToolbar(brainbit, manifest, ui) {
  const actions = Object.fromEntries((manifest?.capability_config?.admin_actions?.actions || []).map((action) => [action.key, action]));
  const selectAction = actions.select_device;
  const scanAction = actions.scan_devices;
  const contactAction = actions.check_contact;
  const instanceConfig = selectAction?.instances || {};
  const candidates = (instanceConfig.status_paths || [])
    .map((path) => readPath(brainbit, path))
    .find((value) => Array.isArray(value)) || [];
  const options = candidates.map((instance) => {
    const payload = {};
    Object.entries(instanceConfig.payload_map || {}).forEach(([target, source]) => {
      const value = readPath(instance, source);
      if (value !== undefined && value !== null && value !== '') payload[target] = value;
    });
    const label = [...new Set((instanceConfig.label_fields || []).map((path) => readPath(instance, path)).filter(Boolean))].join(' - ');
    const serial = String(payload.serial_number || '').trim().toLowerCase();
    const address = String(payload.address || '').replace(/[:-]/g, '').trim().toLowerCase();
    const identity = serial ? `serial:${serial}` : address ? `address:${address}` : '';
    return `<option data-option-key="${ui.escapeHtml(identity)}" value="${ui.escapeHtml(JSON.stringify(payload))}" ${identity ? '' : 'disabled'}>${ui.escapeHtml(label)}</option>`;
  }).join('');
  const selectId = `plugin-action-${manifest?.plugin_key || 'brainbit'}-select_device`;

  const iconAction = (action, icon, order) => {
    if (!action || !manifest) return '';
    const label = ui.t(`plugins.${manifest.plugin_key}.actions.${action.key}`, action.label || action.key);
    return `<button type="button" class="btn-icon-only" style="order:${order}" data-dashboard-action="plugin_admin_action"
        data-plugin-key="${ui.escapeHtml(manifest.plugin_key)}" data-plugin-admin-action="${ui.escapeHtml(action.key)}"
        data-plugin-admin-payload="${ui.escapeHtml(JSON.stringify({}))}"
        title="${ui.escapeHtml(label)}" aria-label="${ui.escapeHtml(label)}"><i class="${icon}"></i></button>`;
  };

  return `<div class="dashboard-toolbar" data-plugin-dashboard-controls>
    <div data-plugin-action-select class="dashboard-toolbar-select-group">
      <select id="${ui.escapeHtml(selectId)}" data-action-key="select_device" class="dashboard-select" style="order:1"
          aria-label="${ui.escapeHtml(ui.t('dashboard.selectDevice', 'Select device'))}">
        <option value="">${ui.escapeHtml(ui.t('dashboard.chooseDevice', 'Choose a device'))}</option>${options}
      </select>
      ${iconAction(selectAction, 'iconoir-link', 4)}
    </div>
    ${iconAction(scanAction, 'iconoir-search', 2)}
    ${iconAction(contactAction, 'iconoir-activity', 3)}
  </div>`;
}

/** Toggle replaces Start/Stop; the icon button next to it replaces Restart. */
function renderRuntimeToggle(brainbit, ui) {
  const running = !!brainbit.can_stop;
  const toggleDisabled = !(brainbit.can_start || brainbit.can_stop) ? 'disabled' : '';
  const restartDisabled = brainbit.can_restart ? '' : 'disabled';
  const toggleLabel = ui.t(running ? 'dashboard.action.stop' : 'dashboard.action.start', running ? 'Stop' : 'Start');
  const restartLabel = ui.t('dashboard.action.restart', 'Restart');
  return `<div class="dashboard-status-row-actions">
    <label class="dashboard-toggle" title="${ui.escapeHtml(toggleLabel)}">
      <span class="switch">
        <input type="checkbox" data-runtime-toggle="${ui.escapeHtml(brainbit.key || '')}" ${running ? 'checked' : ''} ${toggleDisabled}
            aria-label="${ui.escapeHtml(toggleLabel)}">
        <span class="switch-slider"></span>
      </span>
    </label>
    <button type="button" class="btn-icon-only" data-dashboard-action="runtime_${ui.escapeHtml(brainbit.key || '')}_restart" ${restartDisabled}
        title="${ui.escapeHtml(restartLabel)}" aria-label="${ui.escapeHtml(restartLabel)}"><i class="iconoir-refresh"></i></button>
  </div>`;
}

function readPath(source, path) {
  return String(path || '').split('.').reduce(
    (value, key) => (value && typeof value === 'object' ? value[key] : undefined),
    source,
  );
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

function formatMessage(brainbit, latest, ui) {
  if (brainbit.connection_state) {
    const parts = [connectionLabel(brainbit, ui)];
    if (brainbit.connection_state === 'connected') {
      const receiving = ['live', 'receiving'].includes(brainbit.health?.raw_eeg);
      parts.push(ui.t(`brainbit.monitor.${receiving ? 'eegReceiving' : 'checkEeg'}`, receiving ? 'EEG is arriving' : 'EEG not currently arriving'));
      if (brainbit.low_battery) parts.push(ui.t('brainbit.monitor.lowBattery', 'low battery'));
      if (brainbit.contact_quality_state === 'poor') parts.push(ui.t('brainbit.monitor.poorContact', 'poor contact at last measurement'));
      if (latest.artifact?.both_now || latest.artifact?.sequence) parts.push(ui.t('brainbit.monitor.artifacts', 'Live artifacts'));
      if (latest.derived_error) parts.push(ui.t('brainbit.monitor.derivedUnavailable', 'Derived metrics unavailable; raw EEG is independent'));
      if (['START', 'RESET', 'PROGRESS', 'STALLED'].includes(latest.calibration?.event)) {
        parts.push(ui.t('brainbit.monitor.calibrating', 'Derived metrics are calibrating'));
      }
    } else if (brainbit.connection_state === 'failed' && latest.last_message) {
      parts.push(ui.t(latest.status_detail_key || '', latest.last_message));
    }
    return parts.map((part) => ui.escapeHtml(part)).join(' · ');
  }
  const fallback = latest.last_message || brainbit.last_message || '-';
  const detailKey = latest.status_detail_key || brainbit.status_detail_key;
  const hintKey = latest.status_detail_hint_key || brainbit.status_detail_hint_key;
  const message = detailKey ? ui.t(detailKey, fallback) : fallback;
  const hint = hintKey ? ui.t(hintKey, '') : '';
  return hint
    ? `${ui.escapeHtml(message)}<br><span class="status-muted">${ui.escapeHtml(hint)}</span>`
    : ui.escapeHtml(message);
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
