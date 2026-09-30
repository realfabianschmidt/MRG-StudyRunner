/** Optional trusted dashboard renderer for the AM Hub plugin. */

// Shows monitor.py's view of the frames that actually arrived; never adds XDF samples.
const HEADROOM_FACTOR = 1.15;

const TREND_CONFIG = {
  movement: {
    names: ['presMoveEnergy', 'presStaticEnergy'],
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
    names: ['heartBpm', 'breathRate'],
    colors: ['#b2182b', '#2166ac'],
    signed: false,
    minStep: 10,
    unit: ' /min',
    titleFallback: 'Heart & breathing rate',
  },
};

// Headline values: [channel, i18n key, fallback label, unit].
const VITAL_TILES = [
  ['presMoveEnergy', 'movement', 'Movement', ''],
  ['breathRate', 'breathRate', 'Breathing rate', ' /min'],
  ['heartBpm', 'heartRate', 'Heart rate', ' BPM'],
];

function computeDisplayMax(points, end, names, minStep) {
  let recentMax = 0;
  for (const point of points) {
    if (point.at < end - 60 || point.at > end) continue;
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
  const end = now;
  const title = ui.t(`amHub.monitor.${kind}`, titleFallback);
  const displayMax = computeDisplayMax(points, end, names, minStep);
  const paths = names.map((name, index) => {
    let d = '', previous = null;
    for (const point of points) {
      const value = point.values?.[name];
      if (point.at < end - 60 || point.at > end || !Number.isFinite(value)) { previous = null; continue; }
      const x = 32 + (point.at - (end - 60)) / 60 * 306;
      const y = signed ? 64 - (value / displayMax) * 46 : 110 - Math.max(0, value) / displayMax * 92;
      d += `${previous !== null && point.at - previous < 0.5 ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `;
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
  const svg = `<svg viewBox="0 0 360 140" width="100%" role="img" aria-label="${ui.escapeHtml(title)}">
      ${axisLine}
      <g fill="currentColor" font-size="10"><text x="2" y="22">${topLabel}</text><text x="2" y="114">${bottomLabel}</text>
      <text x="32" y="130">−60 s</text><text x="318" y="130">0 s</text></g>${paths}</svg>`;
  // How the preview works and how old it is: in the (i), not under the graph.
  const info = [
    ui.t('amHub.monitor.previewNote', '60-second display of received board frames; no generated samples.'),
    last ? `${ui.t('amHub.monitor.age', 'Age')}: ${Math.max(0, now - last.received_at).toFixed(0)} s` : '',
  ].filter(Boolean).join('\n');
  return ui.graphSection({ title, svg, legend, info });
}

export function renderDashboard({ plugin: amHub }, ui) {
  if (!amHub.enabled || !amHub.running) {
    const message = amHub.status === 'failed'
      ? (amHub.api_unsupported
        ? ui.t('amHub.monitor.v2Required', 'AM Hub API v2 is required; this hub only offers an older stream.')
        : amHub.last_message)
      : ui.t('amHub.monitor.off', 'AM Hub is off. Live measurements are hidden.');
    return `<p class="status-muted" role="status">${ui.escapeHtml(message)}</p>`;
  }
  const latest = amHub.latest || {};

  // Status and the switch are drawn by the shared connection panel.
  return `
    <p class="status-muted" role="status">${renderPerson(amHub, ui)}</p>
    ${renderHostWarning(amHub, ui)}
    ${renderBoardLine(amHub, ui)}
    ${renderVitalTiles(amHub, ui)}
    ${renderTrend('movement', amHub, ui)}
    ${renderTrend('vitals', amHub, ui)}
    ${renderTrend('position', amHub, ui)}
    <details><summary>${ui.escapeHtml(ui.t('amHub.monitor.details', 'Acquisition details'))}</summary>
    <dl class="status-list">
      <dt>${ui.fieldLabel('baseUrl', 'AM Hub URL')}</dt><dd>${ui.escapeHtml(amHub.base_url || '-')}</dd>
      <dt>${ui.fieldLabel('presence', 'Presence')}</dt><dd>${ui.formatBoolean(latest.presence)}${formatPresenceState(latest, ui)}</dd>
      <dt>${ui.fieldLabel('presenceDistance', 'Presence distance')}</dt><dd>${ui.formatValue(latest.presDist, ' mm')}</dd>
      <dt>${ui.fieldLabel('movement', 'Movement / static energy')}</dt><dd>${ui.formatSensorChannels(latest, ['presMoveEnergy', 'presStaticEnergy'])}</dd>
      <dt>${ui.fieldLabel('targetCount', 'Targets')}</dt><dd>${ui.formatValue(latest.targetCount)}</dd>
      <dt>${ui.fieldLabel('position', 'Nearest target')}</dt><dd>${formatPosition(latest, ui)}</dd>
      <dt>${ui.fieldLabel('targets', 'Tracked targets')}</dt><dd>${formatTargets(latest, ui)}</dd>
      <dt>${ui.fieldLabel('vitals', 'Vitals (heart / breath)')}</dt><dd>${ui.formatSensorChannels(latest, ['heartBpm', 'breathRate'])}</dd>
      <dt>${ui.fieldLabel('amHubBoards', 'Boards (link, rate, latency, lost)')}</dt><dd>${formatBoards(amHub, ui)}</dd>
      <dt>${ui.fieldLabel('amHubLink', 'Hub round trip / losses')}</dt><dd>${formatHubLink(amHub, ui)}</dd>
      <dt>${ui.fieldLabel('amHubConnection', 'Connection to the hub')}</dt><dd>${formatConnection(amHub, ui)}</dd>
      <dt>${ui.fieldLabel('lastActive', 'Last active')}</dt><dd>${ui.formatTimestampAge(amHub.last_activity_at, amHub.seconds_since_last_activity)}</dd>
      <dt>${ui.fieldLabel('amHubDataLsl', 'AM Hub data LSL')}</dt><dd>${ui.formatEnabled(amHub.lsl_enabled)}</dd>
    </dl>
    </details>
    ${renderAllTopics(amHub, ui)}
  `;
}

const PERSON_SOURCES = {
  presence: ['amHub.person.presence', 'presence sensor'],
  position: ['amHub.person.position', 'radar position'],
  vitals: ['amHub.person.vitals', 'heart / breathing'],
};
const BOARD_LABELS = {
  radar: ['amHub.board.radar', 'Radar'],
  bio: ['amHub.board.bio', 'Vital signs'],
  solenoid: ['amHub.board.solenoid', 'Valves'],
};

/** "Person detected – via radar position · heart / breathing", from every source the hub has. */
function renderPerson(amHub, ui) {
  if (amHub.status === 'waiting' && amHub.link?.state === 'open') {
    return ui.escapeHtml(ui.t('amHub.monitor.waitingBoards', 'Hub connected; waiting for fresh radar and bio frames.'));
  }
  if (!['connected', 'no_presence'].includes(amHub.status)) return formatMessage(amHub, ui);
  const person = amHub.person || {};
  if (!person.detected) return ui.escapeHtml(ui.t('amHub.person.none', 'No person detected by any sensor.'));
  const sources = (person.sources || [])
    .map((source) => ui.t(...(PERSON_SOURCES[source] || [source, source])))
    .join(' · ');
  return `<strong>${ui.escapeHtml(ui.t('amHub.person.detected', 'Person detected'))}</strong> – ${ui.escapeHtml(ui.t('amHub.person.via', 'via'))} ${ui.escapeHtml(sources)}`;
}

/** WiFi power saving on the Pi causes the dropouts; the hub says whether it is on. */
function renderHostWarning(amHub, ui) {
  if (amHub.hub_host?.wifi_power_save !== 'on') return '';
  return `<p class="status-warning" data-am-hub-powersave>${ui.escapeHtml(ui.t('amHub.host.powerSaveOn',
    'WiFi power saving is on at the AM Hub – this causes dropouts. On the Pi run: sudo bash deploy/install-network-helpers.sh'))}</p>`;
}

/** One line: each board with a live dot, its frame rate and, when silent, how long. */
function renderBoardLine(amHub, ui) {
  const boards = amHub.boards || {};
  const entries = ['radar', 'bio'].map((role) => [role, boards[role] || { live: false, age_s: null }]);
  const items = entries.map(([board, info]) => {
    const label = ui.t(...(BOARD_LABELS[board] || [board, board]));
    const disconnected = amHub.hub_boards?.[board]?.connected === false;
    const live = info.live && !disconnected;
    const dot = `<span aria-hidden="true" style="color:${live ? 'var(--pos)' : 'var(--ink-30)'}">●</span>`;
    const detail = disconnected
      ? ui.escapeHtml(ui.t('amHub.board.disconnected', 'disconnected'))
      : live
        ? ui.formatValue(info.rate_hz, ' Hz')
        : `${ui.escapeHtml(ui.t('amHub.board.silent', 'silent'))} ${info.age_s == null ? '' : ui.formatValue(info.age_s, ' s')}`;
    return `${dot} ${ui.escapeHtml(label)} ${detail}`;
  });
  return `<p class="status-muted" data-am-hub-boards>${items.join(' · ')}</p>`;
}

/** Events per second, how long the stream holds, reconnects and the last loss. */
function formatConnection(amHub, ui) {
  const link = amHub.link || {};
  const parts = [
    `${ui.formatValue(link.events_per_s, ' events/s')}`,
    `${ui.escapeHtml(ui.t('amHub.link.connectedFor', 'connected for'))} ${ui.formatValue(link.connected_for_s, ' s')}`,
    `${ui.escapeHtml(ui.t('amHub.link.reconnects', 'reconnects'))} ${ui.formatValue(link.reconnects)}`,
  ];
  if (link.last_loss) {
    parts.push(`${ui.escapeHtml(ui.t('amHub.link.lastLoss', 'last loss'))} ${ui.escapeHtml(link.last_loss.at)} (${ui.escapeHtml(link.last_loss.reason)})`);
  }
  if (link.last_error && link.state !== 'open') parts.push(ui.escapeHtml(link.last_error));
  return parts.join(' · ');
}

/** Every value the hub sent, as sent: address, raw value, age. Unknown topics are marked. */
function renderAllTopics(amHub, ui) {
  const topics = Object.entries(amHub.topics || {});
  if (!topics.length) return '';
  const unknown = new Set(amHub.unknown_topics || []);
  const rows = topics.map(([address, entry]) => `<dt>${ui.escapeHtml(address)}${unknown.has(address) ? ' *' : ''}</dt>`
    + `<dd>${ui.formatValue(entry.value)} <span class="status-muted">(${ui.formatValue(entry.age_s, ' s')})</span></dd>`).join('');
  const note = unknown.size
    ? `<small>* ${ui.escapeHtml(ui.t('amHub.topics.unknownNote', 'unknown to this display; preserved in hub_events'))}</small>`
    : '';
  return `<details><summary>${ui.escapeHtml(ui.t('amHub.topics.title', 'All values from the hub'))} (${topics.length})</summary>
    <dl class="status-list">${rows}</dl>${note}</details>`;
}

/** Latest movement, breathing and heart rate at a glance; "-" when missing or stale. */
function renderVitalTiles(amHub, ui) {
  const latest = amHub.latest || {};
  const stale = !['connected', 'no_presence'].includes(amHub.status);
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
    const link = board.connected
      ? String(board.transport || '').toUpperCase() || ui.t('amHub.board.online', 'online')
      : ui.t('amHub.board.offline', 'offline');
    const label = ui.t(...(BOARD_LABELS[role] || [role, role]));
    return `${ui.escapeHtml(label)}: ${ui.escapeHtml(link)}, ${ui.formatValue(board.rate_hz, ' Hz')}, `
      + `${ui.formatValue(board.latency_ms, ' ms')}, ${ui.escapeHtml(ui.t('amHub.board.lost', 'lost'))} ${ui.formatValue(board.lost)}`;
  });
  return rows.length ? rows.join('<br>') : '-';
}

function formatHubLink(amHub, ui) {
  const quality = amHub.data_quality || {};
  const seqGaps = Object.values(quality.seq_gaps || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  return `${ui.formatValue(amHub.hub_rtt_ms, ' ms')} · ${ui.escapeHtml(ui.t('amHub.link.seqGaps', 'seq gaps'))} ${ui.formatValue(seqGaps)}`
    + ` · ${ui.escapeHtml(ui.t('amHub.link.hubDropped', 'hub dropped'))} ${ui.formatValue(quality.hub_dropped_events)}`;
}

function formatTargets(latest, ui) {
  const rows = [1, 2, 3]
    .map((index) => {
      const x = latest[`t${index}x`];
      const y = latest[`t${index}y`];
      const speed = latest[`t${index}speed`];
      if (x === null || x === undefined) return null;
      return `t${index}: x ${ui.formatValue(x, ' mm')}, y ${ui.formatValue(y, ' mm')}, ${ui.escapeHtml(ui.t('amHub.target.speed', 'speed'))} ${ui.formatValue(speed, ' cm/s')}`;
    })
    .filter(Boolean);
  return rows.length ? rows.join('<br>') : '-';
}
