/**
 * Turning a recording into drawable timeline tracks.
 *
 * Pure on purpose: no DOM, no fetch, no globals. Everything here is decided
 * from the stream's own LSL header as it came out of the XDF - the stream name,
 * its nominal rate, its channel labels, types and units. Nothing keys off a
 * sensor name, so a plugin added next year gets a labelled, correctly drawn
 * track without this file or any manifest changing.
 *
 * The renderer in admin/session-timeline.js consumes what these functions
 * return; the maths is here so it can be tested without a browser.
 */

/**
 * Above this many samples per second a channel is drawn as a filled waveform
 * rather than a line. A wave needs enough samples per pixel for its envelope to
 * mean something; below it, a line between readings is the honest picture.
 */
export const CONTINUOUS_RATE_HZ = 20;

/**
 * LSL channel types that are continuous by definition, whatever the declared
 * rate says. Names follow the LSL metadata convention.
 * https://github.com/sccn/xdf/wiki/Meta-Data
 */
const CONTINUOUS_TYPES = new Set([
  'eeg', 'ecg', 'ekg', 'emg', 'eog', 'gsr', 'eda', 'ppg', 'respiration',
  'resp', 'audio', 'accelerometer', 'gyroscope', 'magnetometer', 'force',
]);

/** Channels that describe the recording rather than the participant. */
const BOOKKEEPING_CHANNEL = /(^|[._])(seq|sequence|index|counter|dropped|jitter|interval|timestamp|epoch|_ms$|byte_count|version|flags?)([._]|$)/i;

export const TRACK_KIND = { WAVEFORM: 'waveform', LINE: 'line', EVENT: 'event' };

/**
 * Group streams into the track groups the timeline draws.
 *
 * @param {Array} streams  descriptors from the signals API, each with
 *   `{stream_key, stream_name, plugin_key, nominal_rate_hz, channels,
 *     channel_types, channel_units, points, mode}`
 * @returns {Array} one entry per stream, each with its classified tracks
 */
export function buildTrackGroups(streams, options = {}) {
  const preferredOrder = options.preferredChannels || (() => []);
  return (streams || [])
    .map((stream) => {
      const points = normalizePoints(stream.points, stream.mode);
      const channels = selectChannels(
        discoverChannels(stream, points),
        preferredOrder(stream.stream_key || stream.sensor),
      );
      const tracks = channels
        .map((channel) => buildTrack(stream, channel, points))
        .filter(Boolean);
      if (!tracks.length) return null;
      return {
        key: String(stream.stream_key || stream.sensor || ''),
        label: streamLabel(stream),
        pluginKey: String(stream.plugin_key || ''),
        rateHz: Number(stream.nominal_rate_hz) || 0,
        tracks,
      };
    })
    .filter(Boolean);
}

/**
 * Which channels a stream actually offers.
 *
 * The header is the source of truth. Only when a stream declares none - a
 * plugin that publishes an unlabelled numeric stream - do we fall back to
 * whatever numeric keys the samples carry.
 */
export function discoverChannels(stream, points) {
  const declared = (stream?.channels || []).map(String).filter(Boolean);
  if (declared.length) return declared.filter((channel) => !BOOKKEEPING_CHANNEL.test(channel));

  const seen = new Set();
  for (const point of points || []) {
    for (const key of Object.keys(point.min || {})) seen.add(key);
  }
  return [...seen].filter((channel) => !BOOKKEEPING_CHANNEL.test(channel)).sort();
}

/**
 * How a channel should be drawn.
 *
 * Rate first, declared type second - a stream that says it is EEG is continuous
 * even if it forgot to declare a rate.
 */
export function classifyChannel(stream, channel) {
  const declaredType = String(stream?.channel_types?.[channel] || '').toLowerCase();
  if (declaredType === 'markers' || declaredType === 'marker') return TRACK_KIND.EVENT;
  if (CONTINUOUS_TYPES.has(declaredType)) return TRACK_KIND.WAVEFORM;
  const rate = Number(stream?.nominal_rate_hz);
  if (Number.isFinite(rate) && rate >= CONTINUOUS_RATE_HZ) return TRACK_KIND.WAVEFORM;
  return TRACK_KIND.LINE;
}

function buildTrack(stream, channel, points) {
  const values = [];
  for (const point of points) {
    const min = Number(point.min?.[channel]);
    const max = Number(point.max?.[channel]);
    if (!Number.isFinite(min) || !Number.isFinite(max)) continue;
    values.push({ epoch: point.epoch, min, max });
  }
  if (values.length < 2) return null;

  let low = Infinity;
  let high = -Infinity;
  for (const value of values) {
    if (value.min < low) low = value.min;
    if (value.max > high) high = value.max;
  }
  return {
    channel,
    label: channel,
    unit: String(stream?.channel_units?.[channel] || ''),
    kind: classifyChannel(stream, channel),
    banded: (stream.mode || '') === 'min_max_envelope',
    values,
    low,
    high,
  };
}

/**
 * A plugin that declares preferred channels is curating: show exactly those,
 * in its order. A sensor recording twenty fields would otherwise arrive as
 * twenty tracks, most of them noise.
 *
 * Declaring nothing is the other supported case, not a broken one - a plugin
 * added later with no timeline metadata still gets every meaningful channel
 * discovered from its own stream header.
 */
function selectChannels(available, preferred) {
  const curated = (preferred || []).filter((channel) => available.includes(channel));
  return curated.length ? curated : available;
}

function streamLabel(stream) {
  return String(stream?.stream_name || stream?.stream_key || stream?.sensor || '').trim() || 'Stream';
}

/**
 * Bring both response shapes into one form: each point has an epoch plus a min
 * and a max per numeric channel (identical values when samples are raw).
 */
export function normalizePoints(points, mode) {
  if (!Array.isArray(points)) return [];
  if (mode === 'min_max_envelope') {
    return points
      .map((point) => ({ epoch: midEpoch(point), min: point.min || {}, max: point.max || {} }))
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
  for (const key of ['_epoch', 'server_received_epoch', 'processed_epoch']) {
    const value = Number(sample?.[key]);
    if (Number.isFinite(value)) return value;
  }
  return NaN;
}

function flattenNumeric(value, prefix = '', out = {}) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      flattenNumeric(child, prefix ? `${prefix}.${key}` : key, out);
    }
  } else if (typeof value === 'number' && Number.isFinite(value)) {
    out[prefix] = value;
  }
  return out;
}

/** The full extent of everything on the timeline, signals and markers alike. */
export function fullExtent(groups, markers) {
  let start = Infinity;
  let end = -Infinity;
  const note = (epoch) => {
    if (!Number.isFinite(epoch)) return;
    if (epoch < start) start = epoch;
    if (epoch > end) end = epoch;
  };
  for (const group of groups || []) {
    for (const track of group.tracks) {
      note(track.values[0]?.epoch);
      note(track.values[track.values.length - 1]?.epoch);
    }
  }
  for (const marker of markers || []) {
    note(marker.start);
    note(marker.end);
  }
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return { start, end, span: end - start };
}

export const MIN_WINDOW_SECONDS = 0.5;

/**
 * Zoom a window around a fixed point.
 *
 * `anchorRatio` is where the cursor sits in the current window (0 = left edge,
 * 1 = right edge). Keeping that ratio is what makes the sample under the
 * pointer stay put while everything else scales around it.
 */
export function zoomWindow(window, factor, anchorRatio, extent) {
  const span = window.end - window.start;
  const anchorEpoch = window.start + span * clamp(anchorRatio, 0, 1);
  const targetSpan = clamp(span * factor, MIN_WINDOW_SECONDS, extent.span);
  const start = anchorEpoch - (anchorEpoch - window.start) * (targetSpan / span);
  return clampWindow({ start, end: start + targetSpan }, extent);
}

/** Slide a window without resizing it. */
export function panWindow(window, deltaSeconds, extent) {
  return clampWindow(
    { start: window.start + deltaSeconds, end: window.end + deltaSeconds },
    extent,
  );
}

/** Keep a window inside the recording, preserving its span. */
export function clampWindow(window, extent) {
  const span = Math.min(window.end - window.start, extent.span);
  let start = window.start;
  if (start < extent.start) start = extent.start;
  if (start + span > extent.end) start = extent.end - span;
  return { start, end: start + span };
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}
