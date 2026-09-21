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
};

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

  return `
    <div class="dashboard-status-row">
      <div class="dashboard-status-row-info">
        <span class="status-pill status-pill--${ui.escapeHtml(amHub.status || 'unknown')}">${ui.escapeHtml(ui.statusLabel(amHub.status))}</span>
        <strong>${ui.formatEnabled(amHub.configured_enabled ?? amHub.enabled)}</strong>
      </div>
      ${renderRuntimeToggle(amHub, ui)}
    </div>
    <p role="status">${formatMessage(amHub, ui)}</p>
    ${renderTrend('movement', amHub, ui)}
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
      <dt>${ui.fieldLabel('lastActive', 'Last active')}</dt><dd>${ui.formatTimestampAge(latest.server_received_at || amHub.last_activity_at, amHub.seconds_since_last_activity)}</dd>
      <dt>${ui.fieldLabel('amHubDataLsl', 'AM Hub data LSL')}</dt><dd>${ui.formatEnabled(amHub.lsl_enabled)}</dd>
    </dl>
    </details>
  `;
}

/** Toggle replaces Start/Stop; the icon button next to it replaces Restart. */
function renderRuntimeToggle(amHub, ui) {
  const running = !!amHub.can_stop;
  const toggleDisabled = !(amHub.can_start || amHub.can_stop) ? 'disabled' : '';
  const restartDisabled = amHub.can_restart ? '' : 'disabled';
  const toggleLabel = ui.t(running ? 'dashboard.action.stop' : 'dashboard.action.start', running ? 'Stop' : 'Start');
  const restartLabel = ui.t('dashboard.action.restart', 'Restart');
  return `<div class="dashboard-status-row-actions">
    <label class="dashboard-toggle" title="${ui.escapeHtml(toggleLabel)}">
      <span class="switch">
        <input type="checkbox" data-runtime-toggle="${ui.escapeHtml(amHub.key || '')}" ${running ? 'checked' : ''} ${toggleDisabled}
            aria-label="${ui.escapeHtml(toggleLabel)}">
        <span class="switch-slider"></span>
      </span>
    </label>
    <button type="button" class="btn-icon-only" data-dashboard-action="runtime_${ui.escapeHtml(amHub.key || '')}_restart" ${restartDisabled}
        title="${ui.escapeHtml(restartLabel)}" aria-label="${ui.escapeHtml(restartLabel)}"><i class="iconoir-refresh"></i></button>
  </div>`;
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
