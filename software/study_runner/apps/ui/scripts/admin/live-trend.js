/**
 * The live view of the sensor data contract, drawn the same way for every
 * sensor: one graph per series the manifest declares under `live_view`.
 *
 * The data comes from the plugin's status (`plugin.live`, standardized by
 * the core): per series and channel 120 points, the mean of every 0.5 s
 * over the last 60 s, newest last; `null` where nothing arrived. A series can
 * carry `valid` flags; an invalid point is a gap. Lines join neighbouring
 * points only, a lone point is drawn as a dot. Nothing here is recorded.
 *
 * Pure functions: `ui` brings `t`, `escapeHtml` and `graphSection`
 * (the dashboard's helper object), so this module runs in tests as is.
 */

const COLORS = ['#2166ac', '#b2182b', '#008060', '#7950a3', '#936000', '#4d4d4d'];
const HEADROOM_FACTOR = 1.15;
const X_START = 32;
const X_END = 338;
const DEFAULT_POINTS = 120;
const DEFAULT_WINDOW_S = 60;

/** The manifest's live-view series (empty when the plugin declares none). */
export function liveSeries(manifest) {
  return manifest?.capability_config?.live_view?.series || [];
}

/** Every declared series of one plugin, empty graphs when no data is there. */
export function renderLiveTrends({ manifest, live } = {}, ui) {
  const series = liveSeries(manifest);
  if (!series.length) return '';
  const options = {
    channelKeyPrefix: manifest.capability_config.live_view.channel_key_prefix || '',
    points: Number(live?.points) > 1 ? Number(live.points) : DEFAULT_POINTS,
    windowS: Number(live?.window_s) > 0 ? Number(live.window_s) : DEFAULT_WINDOW_S,
  };
  return series.map((spec) => renderLiveTrend(spec, live?.series?.[spec.key], ui, options)).join('');
}

/** One graph: the series' channels on a shared axis. */
export function renderLiveTrend(spec, data, ui, {
  channelKeyPrefix = '', points = DEFAULT_POINTS, windowS = DEFAULT_WINDOW_S,
} = {}) {
  const signed = spec.axis === 'signed';
  const values = scaledValues(spec, data, points);
  const displayMax = displayMaximum(values, Number(spec.min_span) || 1);
  const xAt = (index) => X_START + (index / Math.max(1, points - 1)) * (X_END - X_START);
  const yAt = (value) => (signed
    ? 64 - (value / displayMax) * 46
    : 110 - (Math.max(0, value) / displayMax) * 92);
  const marks = values.map((series, index) => drawSeries(series, xAt, yAt, COLORS[index % COLORS.length], index));
  const title = ui.t(spec.label_key || `liveView.${spec.key}`, spec.label || spec.key);
  const legend = spec.channels
    .map((name, index) => `<span style="color:${COLORS[index % COLORS.length]}">${ui.escapeHtml(channelLabel(name, channelKeyPrefix, ui))}</span>`)
    .join(' · ');
  const unit = spec.unit_label || '';
  const top = `${signed ? '+' : ''}${formatAxis(displayMax)}${unit}`;
  const bottom = signed ? `−${formatAxis(displayMax)}${unit}` : `0${unit}`;
  const axis = signed
    ? '<path d="M32 64H338" fill="none" stroke="currentColor" opacity=".4" />'
    : '<path d="M32 18V110H338" fill="none" stroke="currentColor" opacity=".4" />';
  const svg = `<svg viewBox="0 0 360 140" width="100%" role="img" aria-label="${ui.escapeHtml(title)}" data-live-series="${ui.escapeHtml(spec.key)}">
      ${axis}
      <g fill="currentColor" font-size="10"><text x="2" y="22">${ui.escapeHtml(top)}</text><text x="2" y="114">${ui.escapeHtml(bottom)}</text>
      <text x="32" y="130">−${windowS} s</text><text x="318" y="130">0 s</text></g>${marks.join('')}</svg>`;
  const info = [
    spec.help_key ? ui.t(spec.help_key, '') : '',
    ui.t('dashboard.live.note', 'Live view: the mean of every 0.5 s over the last 60 s, taken from the samples being recorded. It is not recorded itself.'),
  ].filter(Boolean).join('\n');
  return ui.graphSection({ title, svg, legend, info });
}

/** Per channel: `points` numbers (scaled) or null, gaps where a point is invalid. */
export function scaledValues(spec, data, points = DEFAULT_POINTS) {
  const valid = Array.isArray(data?.valid) ? data.valid.slice(-points) : null;
  const scale = Number(spec.scale) || 1;
  return spec.channels.map((name) => {
    const raw = Array.isArray(data?.channels?.[name]) ? data.channels[name].slice(-points) : [];
    const offset = points - raw.length;
    return Array.from({ length: points }, (_, index) => {
      const value = index >= offset ? raw[index - offset] : null;
      const validIndex = valid ? index - (points - valid.length) : -1;
      if (valid && validIndex >= 0 && valid[validIndex] === false) return null;
      return typeof value === 'number' && Number.isFinite(value) ? value * scale : null;
    });
  });
}

/** The axis maximum: the largest magnitude with headroom, in steps of `minSpan`. */
export function displayMaximum(values, minSpan = 1) {
  let largest = 0;
  values.forEach((series) => series.forEach((value) => {
    if (value !== null && Math.abs(value) > largest) largest = Math.abs(value);
  }));
  return Math.max(minSpan, Math.ceil((largest * HEADROOM_FACTOR) / minSpan) * minSpan);
}

function drawSeries(series, xAt, yAt, color, index) {
  let d = '';
  const dots = [];
  series.forEach((value, position) => {
    if (value === null) return;
    const x = xAt(position).toFixed(2);
    const y = yAt(value).toFixed(2);
    const joined = position > 0 && series[position - 1] !== null;
    const next = position < series.length - 1 && series[position + 1] !== null;
    if (!joined && !next) {
      dots.push(`<circle cx="${x}" cy="${y}" r="1.8" fill="${color}" />`);
      return;
    }
    d += `${joined ? 'L' : 'M'}${x},${y} `;
  });
  const dash = index % 2 ? '5 2' : 'none';
  const path = d ? `<path d="${d.trim()}" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="${dash}" />` : '';
  return path + dots.join('');
}

function channelLabel(name, prefix, ui) {
  return prefix ? ui.t(`${prefix}.${name}`, name) : name;
}

function formatAxis(value) {
  if (value >= 10) return String(Math.round(value));
  return String(Number(value.toFixed(2)));
}
