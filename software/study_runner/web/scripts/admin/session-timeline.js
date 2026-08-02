/**
 * The session timeline: recorded signals as stacked lanes on one shared time
 * axis, with the answered questions as markers above them.
 *
 * Drawn as inline SVG on purpose - no chart library, so it also works on a
 * machine with no internet, like every other page here.
 *
 * The backend may return either raw samples or a min/max envelope for long
 * sessions. An envelope is drawn as a band between the lowest and highest value
 * per time bucket, which is the honest picture: it shows the real range instead
 * of pretending a smooth line was measured.
 */
import { escapeHtml } from '../lib/dom-utils.js';
import { getPluginCatalog } from '../lib/plugin-catalog.js';

const LANE_HEIGHT = 54;
const LANE_GAP = 10;
const MARKER_ROW_HEIGHT = 34;
const AXIS_HEIGHT = 24;
const LABEL_WIDTH = 132;
const RIGHT_PADDING = 16;
const VIEW_WIDTH = 1000;

const HIDDEN_CHANNEL_PATTERNS = [
  /epoch/i, /sequence/i, /timestamp/i, /_ms$/i, /interval/i, /dropped/i,
  /byte_count/i, /width/i, /height/i, /version/i, /^flags/i, /phase$/i,
  /readings/i, /^frame\./i, /question_index/i,
];

const MAX_FALLBACK_CHANNELS = 4;

export function renderSessionTimeline(container, { lanes, markers, labels }) {
  if (!container) return;
  container.innerHTML = '';

  const series = buildSeries(lanes, labels);
  const range = timeRange(series, markers);
  if (!range) {
    container.innerHTML = `<p class="timeline-empty">${escapeHtml(labels.nothingRecorded)}</p>`;
    return;
  }

  const totalHeight = MARKER_ROW_HEIGHT + series.length * (LANE_HEIGHT + LANE_GAP) + AXIS_HEIGHT;
  const plotWidth = VIEW_WIDTH - LABEL_WIDTH - RIGHT_PADDING;
  const toX = (epoch) => LABEL_WIDTH + ((epoch - range.start) / range.span) * plotWidth;

  const parts = [
    `<svg class="timeline-svg" viewBox="0 0 ${VIEW_WIDTH} ${totalHeight}" width="100%" height="${totalHeight}" role="img" aria-label="${escapeHtml(labels.chartLabel)}">`,
  ];

  parts.push(renderMarkerRow(markers, toX, labels));

  series.forEach((lane, index) => {
    const top = MARKER_ROW_HEIGHT + index * (LANE_HEIGHT + LANE_GAP);
    parts.push(renderLane(lane, top, toX, plotWidth));
  });

  parts.push(renderAxis(range, toX, totalHeight - AXIS_HEIGHT, plotWidth));
  parts.push('</svg>');

  container.innerHTML = parts.join('');
  return { markerCount: markers.length, laneCount: series.length };
}

/** Turn the raw per-sensor payloads into drawable lanes. */
function buildSeries(lanes, labels) {
  const series = [];
  (lanes || []).forEach((lane) => {
    const points = normalizePoints(lane.points, lane.mode);
    if (!points.length) return;
    const channels = selectChannels(lane.sensor, points);
    channels.forEach((channel) => {
      const values = points
        .map((point) => ({ epoch: point.epoch, min: point.min[channel], max: point.max[channel] }))
        .filter((value) => Number.isFinite(value.min) && Number.isFinite(value.max));
      if (values.length < 2) return;
      const lows = values.map((value) => value.min);
      const highs = values.map((value) => value.max);
      series.push({
        sensor: lane.sensor,
        channel,
        label: channelLabel(lane.sensor, channel, labels),
        values,
        low: Math.min(...lows),
        high: Math.max(...highs),
        banded: lane.mode === 'min_max_envelope',
      });
    });
  });
  return series;
}

/**
 * Bring both response shapes into one form: each point has an epoch plus a
 * min and max per numeric channel (identical values for raw samples).
 */
function normalizePoints(points, mode) {
  if (!Array.isArray(points)) return [];
  if (mode === 'min_max_envelope') {
    return points
      .map((point) => ({
        epoch: midEpoch(point),
        min: point.min || {},
        max: point.max || {},
      }))
      .filter((point) => Number.isFinite(point.epoch));
  }
  return points
    .map((sample) => {
      const flat = flattenNumeric(sample);
      return { epoch: sampleEpoch(sample), min: flat, max: flat };
    })
    .filter((point) => Number.isFinite(point.epoch));
}

function midEpoch(point) {
  const start = Number(point.start_epoch);
  const end = Number(point.end_epoch);
  if (Number.isFinite(start) && Number.isFinite(end)) return (start + end) / 2;
  return Number.isFinite(start) ? start : Number(end);
}

function sampleEpoch(sample) {
  for (const key of ['server_received_epoch', '_epoch', 'processed_epoch']) {
    const value = Number(sample?.[key]);
    if (Number.isFinite(value)) return value;
  }
  return NaN;
}

function flattenNumeric(value, prefix = '', out = {}) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    Object.entries(value).forEach(([key, child]) => {
      flattenNumeric(child, prefix ? `${prefix}.${key}` : key, out);
    });
  } else if (typeof value === 'number' && Number.isFinite(value)) {
    out[prefix] = value;
  }
  return out;
}

function selectChannels(sensor, points) {
  const available = new Set();
  points.forEach((point) => {
    Object.keys(point.min || {}).forEach((key) => available.add(key));
  });

  const preferred = preferredChannels(sensor).filter((key) => available.has(key));
  if (preferred.length) return preferred;

  return [...available]
    .filter((key) => !HIDDEN_CHANNEL_PATTERNS.some((pattern) => pattern.test(key)))
    .sort()
    .slice(0, MAX_FALLBACK_CHANNELS);
}

function preferredChannels(sensor) {
  const normalizedSensor = String(sensor || '');
  const plugin = getPluginCatalog().plugins.find((candidate) => {
    if (candidate.plugin_key === normalizedSensor) return true;
    const aliases = candidate.ui?.timeline?.lane_aliases;
    return Array.isArray(aliases) && aliases.includes(normalizedSensor);
  });
  const declared = plugin?.ui?.timeline?.preferred_channels;
  return Array.isArray(declared) ? declared : [];
}

function channelLabel(sensor, channel, labels) {
  const key = `${sensor}.${channel}`;
  return labels.channels?.[key] || labels.channels?.[channel] || prettifyChannel(channel);
}

function prettifyChannel(channel) {
  const last = String(channel).split('.').pop() || channel;
  return last.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ');
}

function timeRange(series, markers) {
  const epochs = [];
  series.forEach((lane) => lane.values.forEach((value) => epochs.push(value.epoch)));
  (markers || []).forEach((marker) => {
    if (Number.isFinite(marker.start)) epochs.push(marker.start);
    if (Number.isFinite(marker.end)) epochs.push(marker.end);
  });
  if (epochs.length < 2) return null;
  const start = Math.min(...epochs);
  const end = Math.max(...epochs);
  const span = end - start;
  if (!Number.isFinite(span) || span <= 0) return null;
  return { start, end, span };
}

function renderMarkerRow(markers, toX, labels) {
  if (!markers?.length) return '';
  const parts = [`<g class="timeline-markers" aria-label="${escapeHtml(labels.markersLabel)}">`];
  markers.forEach((marker, index) => {
    const x = toX(marker.start);
    const endX = Number.isFinite(marker.end) ? toX(marker.end) : x;
    const width = Math.max(2, endX - x);
    parts.push(
      `<rect class="timeline-marker-span" x="${x.toFixed(1)}" y="6" width="${width.toFixed(1)}" height="${MARKER_ROW_HEIGHT - 14}" rx="3"></rect>`,
      `<g class="timeline-marker" data-marker-index="${index}" tabindex="0" role="button" aria-label="${escapeHtml(marker.title)}">`,
      `<circle class="timeline-marker-dot" cx="${x.toFixed(1)}" cy="${(MARKER_ROW_HEIGHT / 2).toFixed(1)}" r="7"></circle>`,
      `<text class="timeline-marker-num" x="${x.toFixed(1)}" y="${(MARKER_ROW_HEIGHT / 2 + 3.5).toFixed(1)}" text-anchor="middle">${escapeHtml(String(marker.number))}</text>`,
      `</g>`,
    );
  });
  parts.push('</g>');
  return parts.join('');
}

function renderLane(lane, top, toX, plotWidth) {
  const scaleY = (value) => {
    const span = lane.high - lane.low;
    const usable = LANE_HEIGHT - 12;
    if (!Number.isFinite(span) || span <= 0) return top + LANE_HEIGHT / 2;
    return top + 6 + usable - ((value - lane.low) / span) * usable;
  };

  const parts = [
    `<g class="timeline-lane">`,
    `<rect class="timeline-lane-bg" x="${LABEL_WIDTH}" y="${top}" width="${plotWidth}" height="${LANE_HEIGHT}" rx="6"></rect>`,
    `<text class="timeline-lane-label" x="0" y="${(top + LANE_HEIGHT / 2 - 2).toFixed(1)}">${escapeHtml(lane.label)}</text>`,
    `<text class="timeline-lane-range" x="0" y="${(top + LANE_HEIGHT / 2 + 12).toFixed(1)}">${escapeHtml(formatRange(lane.low, lane.high))}</text>`,
  ];

  if (lane.banded) {
    const upper = lane.values.map((value) => `${toX(value.epoch).toFixed(1)},${scaleY(value.max).toFixed(1)}`);
    const lower = lane.values
      .slice()
      .reverse()
      .map((value) => `${toX(value.epoch).toFixed(1)},${scaleY(value.min).toFixed(1)}`);
    parts.push(`<polygon class="timeline-band" points="${upper.concat(lower).join(' ')}"></polygon>`);
  } else {
    const line = lane.values.map((value) => `${toX(value.epoch).toFixed(1)},${scaleY(value.max).toFixed(1)}`);
    parts.push(`<polyline class="timeline-line" points="${line.join(' ')}"></polyline>`);
  }

  parts.push('</g>');
  return parts.join('');
}

function formatRange(low, high) {
  const format = (value) => (Math.abs(value) >= 100 ? Math.round(value) : Number(value.toFixed(2)));
  return `${format(low)} - ${format(high)}`;
}

function renderAxis(range, toX, top, plotWidth) {
  const ticks = 5;
  const parts = [`<g class="timeline-axis">`,
    `<line class="timeline-axis-line" x1="${LABEL_WIDTH}" y1="${top}" x2="${LABEL_WIDTH + plotWidth}" y2="${top}"></line>`];
  for (let index = 0; index <= ticks; index += 1) {
    const epoch = range.start + (range.span * index) / ticks;
    const x = toX(epoch);
    const anchor = index === 0 ? 'start' : index === ticks ? 'end' : 'middle';
    parts.push(
      `<text class="timeline-axis-label" x="${x.toFixed(1)}" y="${(top + 15).toFixed(1)}" text-anchor="${anchor}">${escapeHtml(formatClock(epoch))}</text>`,
    );
  }
  parts.push('</g>');
  return parts.join('');
}

function formatClock(epochSeconds) {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/**
 * Wire marker clicks. Kept separate from rendering so re-rendering the SVG
 * does not need to know about the popover.
 */
export function bindTimelineMarkers(container, markers, onSelect) {
  if (!container) return;
  container.querySelectorAll('.timeline-marker').forEach((node) => {
    const index = Number(node.dataset.markerIndex);
    const marker = markers[index];
    if (!marker) return;
    const activate = () => onSelect?.(marker, node);
    node.addEventListener('click', activate);
    node.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activate();
      }
    });
  });
}
