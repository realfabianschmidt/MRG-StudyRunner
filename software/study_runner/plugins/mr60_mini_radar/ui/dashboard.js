/** Optional trusted dashboard renderer for the MR60 mini-radar plugin. */
export function renderDashboard({ integration: radar }, ui) {
  const latest = radar.latest || {};
  return `
    <div class="status-row">
      <span class="status-pill status-pill--${ui.escapeHtml(radar.status || 'unknown')}">${ui.escapeHtml(ui.statusLabel(radar.status))}</span>
      <strong>${ui.formatEnabled(radar.configured_enabled)}</strong>
    </div>
    <dl class="status-list">
      <dt>${ui.fieldLabel('connection', 'Connection')}</dt><dd>${ui.escapeHtml(radar.connection_type || '-')}</dd>
      <dt>${ui.fieldLabel('device', 'Device')}</dt><dd>${ui.escapeHtml(radar.device_label || radar.ble_device_name || radar.port || '-')}</dd>
      <dt>${ui.fieldLabel('scanWindow', 'Scan window')}</dt><dd>${ui.formatValue(radar.scan_timeout_seconds, ' s')} (${ui.escapeHtml(radar.scan_mode || 'repeated')})</dd>
      <dt>${ui.fieldLabel('lastScan', 'Last scan')}</dt><dd>${ui.escapeHtml(radar.last_scan_started_at || '-')}</dd>
      <dt>${ui.fieldLabel('nextRetry', 'Next retry')}</dt><dd>${ui.escapeHtml(radar.next_retry_at || '-')}</dd>
      <dt>${ui.fieldLabel('heart', 'Heart')}</dt><dd>${ui.formatValue(latest.heartRate, ' BPM')}</dd>
      <dt>${ui.fieldLabel('breath', 'Breath')}</dt><dd>${ui.formatValue(latest.breathRate, ' /min')}</dd>
      <dt>${ui.fieldLabel('presence', 'Presence')}</dt><dd>${ui.formatBoolean(latest.present)}</dd>
      <dt>${ui.fieldLabel('valid', 'Valid')}</dt><dd>${ui.formatBoolean(latest.valid)}</dd>
      <dt>${ui.fieldLabel('stabilized', 'Stabilized')}</dt><dd>${ui.formatBoolean(latest.stabilized)}</dd>
      <dt>${ui.fieldLabel('distance', 'Distance')}</dt><dd>${ui.formatValue(latest.distance, ' cm')}</dd>
      <dt>${ui.fieldLabel('phases', 'Phases')}</dt><dd>${formatPhases(latest, ui)}</dd>
      <dt>${ui.fieldLabel('sequence', 'Sequence')}</dt><dd>${ui.escapeHtml(latest.sequence_number ?? '-')}</dd>
      <dt>${ui.fieldLabel('drops', 'Drops')}</dt><dd>${formatDrops(latest, ui)}</dd>
      <dt>${ui.fieldLabel('timing', 'Timing')}</dt><dd>${formatTiming(latest, ui)}</dd>
      <dt>${ui.fieldLabel('lastUpdateAge', 'Last update age')}</dt><dd>${ui.formatValue(radar.seconds_since_last_activity, ' s')}</dd>
      <dt>${ui.fieldLabel('message', 'Message')}</dt><dd>${ui.escapeHtml(radar.last_message || '-')}</dd>
    </dl>
    ${ui.renderRuntimeButtons(radar)}
  `;
}

function formatPhases(latest, ui) {
  const parts = [['heart', latest.heartPhase], ['breath', latest.breathPhase], ['total', latest.totalPhase]]
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${key}: ${ui.formatValue(value)}`);
  return parts.length ? parts.join('<br>') : '-';
}

function formatDrops(latest, ui) {
  return `${ui.escapeHtml(latest.dropped_since_previous ?? '-')} last / ${ui.escapeHtml(latest.total_dropped ?? '-')} total`;
}

function formatTiming(latest, ui) {
  const parts = [];
  if (latest.device_interval_ms !== null && latest.device_interval_ms !== undefined) parts.push(`device ${ui.formatValue(latest.device_interval_ms, ' ms')}`);
  if (latest.host_interval_ms !== null && latest.host_interval_ms !== undefined) parts.push(`host ${ui.formatValue(latest.host_interval_ms, ' ms')}`);
  if (latest.jitter_ms !== null && latest.jitter_ms !== undefined) parts.push(`jitter ${ui.formatValue(latest.jitter_ms, ' ms')}`);
  return parts.length ? parts.join('<br>') : '-';
}
