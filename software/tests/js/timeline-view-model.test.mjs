import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CONTINUOUS_RATE_HZ,
  MIN_WINDOW_SECONDS,
  TRACK_KIND,
  buildTrackGroups,
  clampWindow,
  classifyChannel,
  discoverChannels,
  fullExtent,
  normalizePoints,
  panWindow,
  zoomWindow,
} from '../../study_runner/frontend/scripts/shared/timeline-view-model.js';

const rawPoints = (count, base = 1000) =>
  Array.from({ length: count }, (_, index) => ({
    _epoch: base + index,
    heartRate: 70 + index,
    heartPhase: Math.sin(index),
  }));

test('classification uses the declared rate, never a sensor name', () => {
  const fast = { nominal_rate_hz: CONTINUOUS_RATE_HZ + 5, channel_types: {} };
  const slow = { nominal_rate_hz: 1, channel_types: {} };

  assert.equal(classifyChannel(fast, 'anything'), TRACK_KIND.WAVEFORM);
  assert.equal(classifyChannel(slow, 'anything'), TRACK_KIND.LINE);
});

test('a declared continuous type wins over a missing rate', () => {
  const stream = { nominal_rate_hz: 0, channel_types: { Fp1: 'EEG', hr: 'misc' } };

  assert.equal(classifyChannel(stream, 'Fp1'), TRACK_KIND.WAVEFORM);
  assert.equal(classifyChannel(stream, 'hr'), TRACK_KIND.LINE);
});

test('marker streams are events, not lines', () => {
  const stream = { nominal_rate_hz: 0, channel_types: { cue: 'Markers' } };
  assert.equal(classifyChannel(stream, 'cue'), TRACK_KIND.EVENT);
});

test('channels come from the header when it declares them', () => {
  const stream = { channels: ['heartRate', 'breathRate', 'sequence_number'] };
  assert.deepEqual(discoverChannels(stream, []), ['heartRate', 'breathRate']);
});

test('an unlabelled stream falls back to its numeric sample keys', () => {
  const points = normalizePoints(rawPoints(3), 'raw');
  assert.deepEqual(discoverChannels({}, points), ['heartPhase', 'heartRate']);
});

test('bookkeeping channels are never drawn', () => {
  const stream = {
    channels: ['value', 'timestamp_ms', 'sequence_number', 'dropped_since_previous', 'jitter_ms'],
  };
  assert.deepEqual(discoverChannels(stream, []), ['value']);
});

test('an envelope keeps its band, raw samples collapse to one value', () => {
  const enveloped = normalizePoints(
    [{ start_epoch: 10, end_epoch: 20, min: { hr: 60 }, max: { hr: 80 } }],
    'min_max_envelope',
  );
  assert.deepEqual(enveloped, [{ epoch: 15, min: { hr: 60 }, max: { hr: 80 } }]);

  const raw = normalizePoints([{ _epoch: 5, hr: 70 }], 'raw');
  assert.equal(raw[0].min.hr, 70);
  assert.equal(raw[0].max.hr, 70);
});

test('groups carry the stream name and a track per channel', () => {
  const [group] = buildTrackGroups([{
    stream_key: 'mr60',
    stream_name: 'MR60 Mini-radar',
    plugin_key: 'mr60_mini_radar',
    nominal_rate_hz: 10,
    channels: ['heartRate', 'heartPhase'],
    channel_types: { heartPhase: 'ECG' },
    channel_units: { heartRate: 'bpm' },
    mode: 'raw',
    points: rawPoints(5),
  }]);

  assert.equal(group.label, 'MR60 Mini-radar');
  assert.deepEqual(group.tracks.map((track) => track.channel), ['heartRate', 'heartPhase']);
  assert.equal(group.tracks[0].unit, 'bpm');
  // 10 Hz is below the continuous threshold, but ECG is continuous regardless.
  assert.equal(group.tracks[0].kind, TRACK_KIND.LINE);
  assert.equal(group.tracks[1].kind, TRACK_KIND.WAVEFORM);
});

test('a plugin that declares preferred channels shows only those', () => {
  const stream = {
    stream_key: 'mr60',
    channels: ['a', 'b', 'c'],
    mode: 'raw',
    points: Array.from({ length: 4 }, (_, i) => ({ _epoch: i, a: i, b: i, c: i })),
  };

  const [curated] = buildTrackGroups([stream], { preferredChannels: () => ['c', 'a'] });
  assert.deepEqual(curated.tracks.map((track) => track.channel), ['c', 'a']);

  // Declaring nothing is the other supported case: discover everything.
  const [discovered] = buildTrackGroups([stream]);
  assert.deepEqual(discovered.tracks.map((track) => track.channel), ['a', 'b', 'c']);
});

test('a channel with fewer than two readings is not a track', () => {
  const groups = buildTrackGroups([{
    stream_key: 's', channels: ['only'], mode: 'raw', points: [{ _epoch: 1, only: 5 }],
  }]);
  assert.deepEqual(groups, []);
});

test('extent spans signals and markers together', () => {
  const groups = buildTrackGroups([{
    stream_key: 's', channels: ['v'], mode: 'raw',
    points: [{ _epoch: 100, v: 1 }, { _epoch: 200, v: 2 }],
  }]);
  const extent = fullExtent(groups, [{ start: 50, end: 260 }]);

  assert.equal(extent.start, 50);
  assert.equal(extent.end, 260);
  assert.equal(extent.span, 210);
});

test('extent is null when there is nothing to place', () => {
  assert.equal(fullExtent([], []), null);
});

const extent = { start: 0, end: 100, span: 100 };

test('zooming keeps the epoch under the cursor in place', () => {
  const window = { start: 0, end: 100 };
  // Cursor at 25% of the window is epoch 25; halving the span must keep it there.
  const zoomed = zoomWindow(window, 0.5, 0.25, extent);

  assert.equal(zoomed.end - zoomed.start, 50);
  assert.equal(zoomed.start + (zoomed.end - zoomed.start) * 0.25, 25);
});

test('zooming out never escapes the recording', () => {
  const zoomed = zoomWindow({ start: 40, end: 60 }, 100, 0.5, extent);

  assert.equal(zoomed.start, 0);
  assert.equal(zoomed.end, 100);
});

test('zooming in stops at the minimum window', () => {
  const zoomed = zoomWindow({ start: 0, end: 100 }, 0.0001, 0.5, extent);
  assert.equal(zoomed.end - zoomed.start, MIN_WINDOW_SECONDS);
});

test('panning past an edge sticks to it and keeps the span', () => {
  const left = panWindow({ start: 10, end: 30 }, -999, extent);
  assert.deepEqual(left, { start: 0, end: 20 });

  const right = panWindow({ start: 10, end: 30 }, 999, extent);
  assert.deepEqual(right, { start: 80, end: 100 });
});

test('a window wider than the recording is clamped to it', () => {
  assert.deepEqual(clampWindow({ start: -50, end: 500 }, extent), { start: 0, end: 100 });
});
