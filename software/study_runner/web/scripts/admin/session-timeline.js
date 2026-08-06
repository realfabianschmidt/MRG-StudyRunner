/**
 * The session timeline: one shared left-to-right time axis, every recorded
 * stream stacked above it as a track, the answered questions as markers on top.
 *
 * Laid out like a cutting room: a sticky label column on the left, the tracks
 * scrolling and zooming together on the right, and a playhead that reads out
 * every track at the moment under the cursor.
 *
 * What gets drawn is decided entirely in lib/timeline-view-model.js from the
 * LSL header the XDF carries, so this file knows nothing about any particular
 * sensor. Drawn as inline SVG - no chart library, because the lab network has
 * no internet.
 */
import { escapeHtml } from '../lib/dom-utils.js';
import { getPluginCatalog } from '../lib/plugin-catalog.js';
import {
  TRACK_KIND,
  buildTrackGroups,
  fullExtent,
  panWindow,
  zoomWindow,
} from '../lib/timeline-view-model.js';

const TRACK_HEIGHT = 44;
const TRACK_GAP = 4;
const GROUP_HEADER_HEIGHT = 26;
const MARKER_ROW_HEIGHT = 34;
const AXIS_HEIGHT = 24;
const PLOT_WIDTH = 1000;
const ZOOM_STEP = 0.0015;

/** Live state for the mounted timeline. One session view, one timeline. */
let view = null;

/**
 * Draw the timeline and wire its interaction.
 *
 * @param {HTMLElement} container
 * @param {object} model  `{streams, markers, labels}` - streams are signal
 *   payloads carrying both their descriptor and their points.
 * @param {object} handlers  `{onWindowChange}` - called (debounced) with the
 *   visible window so the caller can refetch it at full resolution.
 */
export function renderSessionTimeline(container, { streams, markers, labels }, handlers = {}) {
  if (!container) return null;

  const groups = buildTrackGroups(streams, { preferredChannels });
  const extent = fullExtent(groups, markers);
  if (!extent) {
    container.innerHTML = `<p class="timeline-empty">${escapeHtml(labels.nothingRecorded)}</p>`;
    view = null;
    return null;
  }

  const collapsed = view?.collapsed ?? new Set();
  const window = view?.window && sameExtent(view.extent, extent) ? view.window : { ...extent };
  const onMarkerSelect = view?.onMarkerSelect ?? null;
  view = { container, rawStreams: streams || [], groups, markers: markers || [], labels,
           extent, window, collapsed, handlers, onMarkerSelect };

  repaint();
  return { groupCount: groups.length, trackCount: groups.reduce((n, g) => n + g.tracks.length, 0) };
}

/** Replace one stream's samples in place, e.g. after a zoom refetch. */
export function updateStreamPoints(streamKey, points, mode) {
  if (!view) return;
  const stream = view.rawStreams?.find((candidate) => candidate.stream_key === streamKey);
  if (!stream) return;
  stream.points = points;
  stream.mode = mode;
  view.groups = buildTrackGroups(view.rawStreams, { preferredChannels });
  repaint();
}

/**
 * Redraw and re-wire, always together.
 *
 * `paint()` throws the old DOM away, taking every listener with it. Anything
 * that changes what is on screen goes through here.
 */
function repaint() {
  paint();
  bindInteraction();
}

function sameExtent(a, b) {
  return a && b && a.start === b.start && a.end === b.end;
}

function preferredChannels(streamKey) {
  const plugin = getPluginCatalog().plugins.find((candidate) => {
    if (candidate.plugin_key === streamKey) return true;
    const aliases = candidate.ui?.timeline?.lane_aliases;
    return Array.isArray(aliases) && aliases.includes(streamKey);
  });
  const declared = plugin?.ui?.timeline?.preferred_channels;
  return Array.isArray(declared) ? declared : [];
}

/* ------------------------------------------------------------------ layout */

function visibleRows() {
  const rows = [];
  for (const group of view.groups) {
    rows.push({ type: 'group', group });
    if (view.collapsed.has(group.key)) continue;
    for (const track of group.tracks) rows.push({ type: 'track', group, track });
  }
  return rows;
}

function rowOffsets(rows) {
  const offsets = [];
  let top = MARKER_ROW_HEIGHT;
  for (const row of rows) {
    offsets.push(top);
    top += (row.type === 'group' ? GROUP_HEADER_HEIGHT : TRACK_HEIGHT) + TRACK_GAP;
  }
  return { offsets, total: top + AXIS_HEIGHT };
}

/* ----------------------------------------------------------------- drawing */

function paint() {
  const rows = visibleRows();
  const { offsets, total } = rowOffsets(rows);
  const { window, extent, labels } = view;
  const toX = (epoch) => ((epoch - window.start) / (window.end - window.start)) * PLOT_WIDTH;

  view.container.innerHTML = `
    <div class="timeline-grid" style="--timeline-height:${total}px">
      <div class="timeline-labels">${renderLabels(rows, offsets)}</div>
      <div class="timeline-plot">
        <svg class="timeline-svg" viewBox="0 0 ${PLOT_WIDTH} ${total}" preserveAspectRatio="none"
             role="img" aria-label="${escapeHtml(labels.chartLabel)}">
          ${renderMarkerRow(view.markers, toX, labels)}
          ${rows.map((row, index) => renderRow(row, offsets[index], toX)).join('')}
          ${renderAxis(window, toX, total - AXIS_HEIGHT)}
        </svg>
        <div class="timeline-playhead" hidden></div>
      </div>
    </div>
    <div class="timeline-zoom-hint">${escapeHtml(zoomHint(window, extent, labels))}</div>`;
}

function renderLabels(rows, offsets) {
  return rows.map((row, index) => {
    const top = offsets[index];
    if (row.type === 'group') {
      const isCollapsed = view.collapsed.has(row.group.key);
      return `
        <button class="timeline-group-label" type="button" data-group-key="${escapeHtml(row.group.key)}"
                style="top:${top}px;height:${GROUP_HEADER_HEIGHT}px"
                aria-expanded="${isCollapsed ? 'false' : 'true'}">
          <i class="${isCollapsed ? 'iconoir-nav-arrow-right' : 'iconoir-nav-arrow-down'}"></i>
          <span>${escapeHtml(row.group.label)}</span>
        </button>`;
    }
    const unit = row.track.unit ? ` <span class="timeline-track-unit">${escapeHtml(row.track.unit)}</span>` : '';
    return `
      <div class="timeline-track-label" style="top:${top}px;height:${TRACK_HEIGHT}px">
        <span class="timeline-track-name">${escapeHtml(row.track.label)}${unit}</span>
        <span class="timeline-track-value" data-readout="${escapeHtml(row.group.key)}::${escapeHtml(row.track.channel)}">${escapeHtml(formatRange(row.track.low, row.track.high))}</span>
      </div>`;
  }).join('');
}

function renderRow(row, top, toX) {
  if (row.type === 'group') return '';
  return renderTrack(row.track, top, toX);
}

function renderTrack(track, top, toX) {
  const span = track.high - track.low;
  const usable = TRACK_HEIGHT - 8;
  const scaleY = (value) => (
    span > 0 ? top + 4 + usable - ((value - track.low) / span) * usable : top + TRACK_HEIGHT / 2
  );

  const parts = [
    `<g class="timeline-track" data-kind="${track.kind}">`,
    `<rect class="timeline-track-bg" x="0" y="${top}" width="${PLOT_WIDTH}" height="${TRACK_HEIGHT}"></rect>`,
  ];

  if (track.kind === TRACK_KIND.EVENT) {
    for (const value of track.values) {
      const x = toX(value.epoch).toFixed(1);
      parts.push(`<line class="timeline-event" x1="${x}" y1="${top + 4}" x2="${x}" y2="${top + TRACK_HEIGHT - 4}"></line>`);
    }
  } else if (track.kind === TRACK_KIND.WAVEFORM || track.banded) {
    // A filled envelope: the real range per bucket, not a smooth line nobody
    // measured. This is what makes an oscillation read as a wave.
    const upper = track.values.map((v) => `${toX(v.epoch).toFixed(1)},${scaleY(v.max).toFixed(1)}`);
    const lower = track.values.slice().reverse().map((v) => `${toX(v.epoch).toFixed(1)},${scaleY(v.min).toFixed(1)}`);
    parts.push(`<polygon class="timeline-wave" points="${upper.concat(lower).join(' ')}"></polygon>`);
  } else {
    const line = track.values.map((v) => `${toX(v.epoch).toFixed(1)},${scaleY((v.min + v.max) / 2).toFixed(1)}`);
    parts.push(`<polyline class="timeline-line" points="${line.join(' ')}"></polyline>`);
  }

  parts.push('</g>');
  return parts.join('');
}

function renderMarkerRow(markers, toX, labels) {
  if (!markers?.length) return '';
  const parts = [`<g class="timeline-markers" aria-label="${escapeHtml(labels.markersLabel)}">`];
  markers.forEach((marker, index) => {
    const x = toX(marker.start);
    const endX = Number.isFinite(marker.end) ? toX(marker.end) : x;
    parts.push(
      `<rect class="timeline-marker-span" x="${x.toFixed(1)}" y="6" width="${Math.max(2, endX - x).toFixed(1)}" height="${MARKER_ROW_HEIGHT - 14}" rx="3"></rect>`,
      `<g class="timeline-marker" data-marker-index="${index}" tabindex="0" role="button" aria-label="${escapeHtml(marker.title)}">`,
      `<circle class="timeline-marker-dot" cx="${x.toFixed(1)}" cy="${MARKER_ROW_HEIGHT / 2}" r="7"></circle>`,
      `<text class="timeline-marker-num" x="${x.toFixed(1)}" y="${MARKER_ROW_HEIGHT / 2 + 3.5}" text-anchor="middle">${escapeHtml(String(marker.number))}</text>`,
      `</g>`,
    );
  });
  parts.push('</g>');
  return parts.join('');
}

function renderAxis(window, toX, top) {
  const ticks = 6;
  const span = window.end - window.start;
  const parts = [`<g class="timeline-axis">`,
    `<line class="timeline-axis-line" x1="0" y1="${top}" x2="${PLOT_WIDTH}" y2="${top}"></line>`];
  for (let index = 0; index <= ticks; index += 1) {
    const epoch = window.start + (span * index) / ticks;
    const anchor = index === 0 ? 'start' : index === ticks ? 'end' : 'middle';
    parts.push(`<text class="timeline-axis-label" x="${toX(epoch).toFixed(1)}" y="${top + 15}" text-anchor="${anchor}">${escapeHtml(formatClock(epoch))}</text>`);
  }
  parts.push('</g>');
  return parts.join('');
}

function formatRange(low, high) {
  const format = (value) => (Math.abs(value) >= 100 ? Math.round(value) : Number(value.toFixed(2)));
  return `${format(low)} – ${format(high)}`;
}

function formatValue(value) {
  return Math.abs(value) >= 100 ? String(Math.round(value)) : String(Number(value.toFixed(2)));
}

function formatClock(epochSeconds) {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function zoomHint(window, extent, labels) {
  const shown = Math.round(window.end - window.start);
  const total = Math.round(extent.span);
  return shown >= total ? labels.zoomFull : labels.zoomPartial.replace('{shown}', shown).replace('{total}', total);
}

/* ------------------------------------------------------------- interaction */

function bindInteraction() {
  const plot = view.container.querySelector('.timeline-plot');
  if (!plot) return;

  if (view.onMarkerSelect) attachMarkerHandlers(view.container, view.markers, view.onMarkerSelect);

  view.container.querySelectorAll('.timeline-group-label').forEach((button) => {
    button.addEventListener('click', () => {
      const key = button.dataset.groupKey;
      if (view.collapsed.has(key)) view.collapsed.delete(key);
      else view.collapsed.add(key);
      repaint();
    });
  });

  plot.addEventListener('wheel', (event) => {
    // Horizontal intent pans, vertical zooms - the convention every editor uses.
    event.preventDefault();
    const rect = plot.getBoundingClientRect();
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      const seconds = ((view.window.end - view.window.start) / rect.width) * event.deltaX;
      view.window = panWindow(view.window, seconds, view.extent);
    } else {
      const anchorRatio = (event.clientX - rect.left) / rect.width;
      view.window = zoomWindow(view.window, Math.exp(event.deltaY * ZOOM_STEP), anchorRatio, view.extent);
    }
    repaint();
    requestWindow();
  }, { passive: false });

  plot.addEventListener('pointermove', (event) => movePlayhead(plot, event));
  plot.addEventListener('pointerleave', () => hidePlayhead(plot));
}

function movePlayhead(plot, event) {
  const rect = plot.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  const epoch = view.window.start + (view.window.end - view.window.start) * ratio;

  const playhead = plot.querySelector('.timeline-playhead');
  if (playhead) {
    playhead.hidden = false;
    playhead.style.left = `${(ratio * 100).toFixed(3)}%`;
    playhead.dataset.time = formatClock(epoch);
  }

  for (const group of view.groups) {
    for (const track of group.tracks) {
      const readout = view.container.querySelector(`[data-readout="${cssEscape(group.key)}::${cssEscape(track.channel)}"]`);
      if (!readout) continue;
      const value = valueAt(track, epoch);
      readout.textContent = value === null ? formatRange(track.low, track.high) : formatValue(value);
    }
  }
}

function hidePlayhead(plot) {
  const playhead = plot.querySelector('.timeline-playhead');
  if (playhead) playhead.hidden = true;
  for (const group of view.groups) {
    for (const track of group.tracks) {
      const readout = view.container.querySelector(`[data-readout="${cssEscape(group.key)}::${cssEscape(track.channel)}"]`);
      if (readout) readout.textContent = formatRange(track.low, track.high);
    }
  }
}

/** Nearest reading to an epoch, or null when the cursor is past the ends. */
function valueAt(track, epoch) {
  let nearest = null;
  let bestDistance = Infinity;
  for (const value of track.values) {
    const distance = Math.abs(value.epoch - epoch);
    if (distance < bestDistance) {
      bestDistance = distance;
      nearest = value;
    }
  }
  if (!nearest) return null;
  return (nearest.min + nearest.max) / 2;
}

function cssEscape(value) {
  return String(value).replace(/["\\]/g, '\\$&');
}

let windowTimer = null;
function requestWindow() {
  if (!view.handlers.onWindowChange) return;
  clearTimeout(windowTimer);
  // Redraw is instant from the data in hand; the sharper window follows.
  windowTimer = setTimeout(() => view.handlers.onWindowChange({ ...view.window }), 220);
}

/**
 * Wire marker clicks. Kept separate from rendering so a repaint does not need
 * to know about the popover.
 */
export function bindTimelineMarkers(container, markers, onSelect) {
  if (!container) return;
  if (view) view.onMarkerSelect = onSelect;
  attachMarkerHandlers(container, markers, onSelect);
}

function attachMarkerHandlers(container, markers, onSelect) {
  container.querySelectorAll('.timeline-marker').forEach((node) => {
    const marker = markers[Number(node.dataset.markerIndex)];
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
