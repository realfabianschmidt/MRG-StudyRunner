/** Optional trusted dashboard renderer for the AM Hub plugin. */

// Ported from the BrainBit dashboard's trend-graph pattern, generalized from
// a fixed 0-100% ratio to arbitrary real units (movement energy stays
// unsigned 0-100; position is signed millimetres, centered on the sensor).
const HEADROOM_FACTOR = 1.15;

const TREND_CONFIG = {
  movement: {
    names: ['moveEnergy', 'staticEnergy'],
    colors: ['#b2182b', '#2166ac'],
    signed: false,
    minStep: 5,
    unit: '',
    titleFallback: 'Movement & presence energy',
  },
  position: {
    names: ['personX', 'personY'],
    colors: ['#2166ac', '#b2182b'],
    signed: true,
    minStep: 100,
    unit: ' mm',
    titleFallback: 'Position (relative to the sensor)',
  },
  vitals: {
    names: ['heartRate', 'breathRate'],
    colors: ['#b2182b', '#2166ac'],
    signed: false,
    minStep: 10,
    unit: ' /min',
    titleFallback: 'Heart & breathing rate',
  },
};

// Headline values: [channel, i18n key, fallback label, unit].
const VITAL_TILES = [
  ['moveEnergy', 'movement', 'Movement', ''],
  ['breathRate', 'breathRate', 'Breathing rate', ' /min'],
  ['heartRate', 'heartRate', 'Heart rate', ' BPM'],
];

function computeDisplayMax(points, end, names, minStep) {
  let recentMax = 0;
  for (const point of points) {
    if (point.at < end - 60 || point.at > end || point.validity !== 'valid') continue;
    for (const name of names) {
      const value = point.values?.[name];
      if (Number.isFinite(value) && Math.abs(value) > recentMax) recentMax = Math.abs(value);
    }
  }
  return Math.max(minStep, Math.ceil(recentMax * HEADROOM_FACTOR / minStep) * minStep);
}

export function renderTrend(kind, plugin, ui, now = Date.now() / 1000) {
  const { names, colors, signed, minStep, unit, titleFallback } = TREND_CONFIG[kind];
  const points = plugin.preview?.[kind] || [];
  const last = points.at(-1);
  const end = last ? last.at + Math.max(0, now - last.received_at) : now;
  const title = ui.t(`amHub.monitor.${kind}`, titleFallback);
  const displayMax = computeDisplayMax(points, end, names, minStep);
  const paths = names.map((name, index) => {
    let d = '', previous = null;
    for (const point of points) {
      const value = point.values?.[name];
      if (point.at < end - 60 || point.at > end || point.validity !== 'valid' || !Number.isFinite(value)) { previous = null; continue; }
      const x = 32 + (point.at - (end - 60)) / 60 * 306;
      const y = signed ? 64 - (value / displayMax) * 46 : 110 - Math.max(0, value) / displayMax * 92;
      d += `${previous !== null && point.at - previous < 1.6 ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `;
      previous = point.at;
    }
    return `<path d="${d}" fill="none" stroke="${colors[index]}" stroke-width="2" stroke-dasharray="${index % 2 ? '5 2' : 'none'}" />`;
  }).join('');
  const legend = names.map((name, index) => `<span style="color:${colors[index]}">${ui.escapeHtml(ui.t(`amHub.channel.${name}`, name))}</span>`).join(' · ');
  const topLabel = `${signed ? '+' : ''}${Math.round(displayMax)}${unit}`;
  const bottomLabel = signed ? `−${Math.round(displayMax)}${unit}` : `0${unit}`;
  const axisLine = signed
    ? '<path d="M32 64H338" fill="none" stroke="currentColor" opacity=".4" />'
    : '<path d="M32 18V110H338" fill="none" stroke="currentColor" opacity=".4" />';
  return `<section aria-label="${ui.escapeHtml(title)}"><strong>${ui.escapeHtml(title)}</strong>
    <svg viewBox="0 0 360 140" width="100%" role="img" aria-label="${ui.escapeHtml(title)}">
      ${axisLine}
      <g fill="currentColor" font-size="10"><text x="2" y="22">${topLabel}</text><text x="2" y="114">${bottomLabel}</text>
      <text x="32" y="130">−60 s</text><text x="318" y="130">0 s</text></g>${paths}</svg>
    <small>${legend}<br>${ui.escapeHtml(ui.t('amHub.monitor.previewNote', '60-second preview, at most 1 Hz.'))}
    ${last ? ` · ${ui.escapeHtml(ui.t('amHub.monitor.age', 'Age'))}: ${Math.max(0, now - last.received_at).toFixed(0)} s` : ''}</small></section>`;
}

export function renderDashboard({ plugin: amHub }, ui) {
  const latest = amHub.latest || {};

  // Status, presence and the switch are drawn by the shared connection panel.
  return `
    <p class="status-muted" role="status">${formatMessage(amHub, ui)}</p>
    ${renderVitalTiles(amHub, ui)}
    ${renderTrend('movement', amHub, ui)}
    ${renderTrend('vitals', amHub, ui)}
    ${renderTrend('position', amHub, ui)}
    <details><summary>${ui.escapeHtml(ui.t('amHub.monitor.details', 'Acquisition details'))}</summary>
    <dl class="status-list">
      <dt>${ui.fieldLabel('baseUrl', 'AM Hub URL')}</dt><dd>${ui.escapeHtml(amHub.base_url || '-')}</dd>
      <dt>${ui.fieldLabel('presence', 'Presence')}</dt><dd>${ui.formatBoolean(latest.presence)}${formatPresenceState(latest, ui)}</dd>
      <dt>${ui.fieldLabel('presenceDistance', 'Presence distance')}</dt><dd>${ui.formatValue(latest.presDist, ' mm')}</dd>
      <dt>${ui.fieldLabel('movement', 'Movement / static energy')}</dt><dd>${ui.formatSensorChannels(latest, ['moveEnergy', 'staticEnergy'])}</dd>
      <dt>${ui.fieldLabel('targetCount', 'Targets')}</dt><dd>${ui.formatValue(latest.targetCount)}</dd>
      <dt>${ui.fieldLabel('position', 'Nearest target')}</dt><dd>${formatPosition(latest, ui)}</dd>
      <dt>${ui.fieldLabel('targets', 'Tracked targets')}</dt><dd>${formatTargets(latest, ui)}</dd>
      <dt>${ui.fieldLabel('vitals', 'Vitals (heart / breath)')}</dt><dd>${ui.formatSensorChannels(latest, ['heartRate', 'breathRate'])}</dd>
      <dt>${ui.fieldLabel('amHubBoards', 'Boards (link, rate, latency, lost)')}</dt><dd>${formatBoards(amHub, ui)}</dd>
      <dt>${ui.fieldLabel('amHubLink', 'Hub round trip / losses')}</dt><dd>${formatHubLink(amHub, ui)}</dd>
      <dt>${ui.fieldLabel('lastActive', 'Last active')}</dt><dd>${ui.formatTimestampAge(latest.server_received_at || amHub.last_activity_at, amHub.seconds_since_last_activity)}</dd>
      <dt>${ui.fieldLabel('amHubDataLsl', 'AM Hub data LSL')}</dt><dd>${ui.formatEnabled(amHub.lsl_enabled)}</dd>
    </dl>
    </details>
  `;
}

/** Latest movement, breathing and heart rate at a glance; "-" when missing or stale. */
function renderVitalTiles(amHub, ui) {
  const latest = amHub.latest || {};
  const stale = amHub.status === 'stale';
  const tiles = VITAL_TILES.map(([channel, key, fallback, unit]) => {
    const value = latest[channel];
    const shown = !stale && Number.isFinite(value) ? ui.formatValue(value, unit) : '-';
    return `<div><small>${ui.escapeHtml(ui.t(`amHub.tile.${key}`, fallback))}</small><br><strong>${shown}</strong></div>`;
  }).join('');
  return `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center">${tiles}</div>`;
}

function formatMessage(amHub, ui) {
  return amHub.last_message ? ui.escapeHtml(amHub.last_message) : '-';
}

function formatPresenceState(latest, ui) {
  if (latest.presState === null || latest.presState === undefined) return '';
  return ` (${ui.escapeHtml(String(latest.presState))})`;
}

function formatPosition(latest, ui) {
  const parts = [];
  if (latest.personDist !== null && latest.personDist !== undefined) {
    parts.push(`${ui.escapeHtml(ui.t('dashboard.distancePrefix', 'distance'))}: ${ui.formatValue(latest.personDist, ' mm')}`);
  }
  if (latest.personX !== null && latest.personX !== undefined) parts.push(`x: ${ui.formatValue(latest.personX, ' mm')}`);
  if (latest.personY !== null && latest.personY !== undefined) parts.push(`y: ${ui.formatValue(latest.personY, ' mm')}`);
  return parts.length ? parts.join(', ') : '-';
}

/** One line per board: connected, transport, rate, end-to-end latency, packets lost this session. */
function formatBoards(amHub, ui) {
  const rows = Object.entries(amHub.hub_boards || {}).map(([role, board]) => {
    const link = board.connected ? String(board.transport || '').toUpperCase() || 'on' : 'offline';
    return `${ui.escapeHtml(role)}: ${ui.escapeHtml(link)}, ${ui.formatValue(board.rate_hz, ' Hz')}, `
      + `${ui.formatValue(board.latency_ms, ' ms')}, lost ${ui.formatValue(board.lost)}`;
  });
  return rows.length ? rows.join('<br>') : '-';
}

function formatHubLink(amHub, ui) {
  const quality = amHub.data_quality || {};
  const seqGaps = Object.values(quality.seq_gaps || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  return `${ui.formatValue(amHub.hub_rtt_ms, ' ms')} · seq gaps ${ui.formatValue(seqGaps)}`
    + ` · hub dropped ${ui.formatValue(quality.hub_dropped_events)}`;
}

function formatTargets(latest, ui) {
  const rows = [1, 2, 3]
    .map((index) => {
      const x = latest[`t${index}x`];
      const y = latest[`t${index}y`];
      const speed = latest[`t${index}speed`];
      if (x === null || x === undefined) return null;
      return `t${index}: x ${ui.formatValue(x, ' mm')}, y ${ui.formatValue(y, ' mm')}, speed ${ui.formatValue(speed, ' mm/s')}`;
    })
    .filter(Boolean);
  return rows.length ? rows.join('<br>') : '-';
}
