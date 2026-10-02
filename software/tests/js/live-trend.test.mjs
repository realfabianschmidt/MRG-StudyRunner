import assert from 'node:assert/strict';
import test from 'node:test';
import {
  displayMaximum,
  liveSeries,
  renderLiveTrend,
  renderLiveTrends,
  scaledValues,
} from '../../study_runner/apps/ui/scripts/admin/live-trend.js';
import { graphSection, infoTip } from '../../study_runner/apps/ui/scripts/shared/dashboard-graph.js';

const ui = {
  escapeHtml: (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;'),
  t: (key, fallback) => fallback,
  graphSection,
  infoTip,
};

const POINTS = 120;
const withTail = (tail) => [...Array(POINTS - tail.length).fill(null), ...tail];
const spec = (overrides = {}) => ({
  key: 'movement', stream: 'radar', channels: ['move', 'still'], axis: 'zero', min_span: 5, scale: 1, unit_label: '',
  ...overrides,
});
const manifest = (series = [spec()]) => ({
  plugin_key: 'fixture',
  capability_config: { live_view: { rate_hz: 2, window_s: 60, series, channel_key_prefix: 'fixture.channel' } },
});

test('a plugin without a live view draws nothing', () => {
  assert.equal(renderLiveTrends({ manifest: { capability_config: {} }, live: null }, ui), '');
  assert.deepEqual(liveSeries(null), []);
});

test('every declared series is drawn, also before any data arrived', () => {
  const html = renderLiveTrends({ manifest: manifest([spec(), spec({ key: 'vitals', channels: ['heart'] })]), live: null }, ui);
  assert.equal((html.match(/class="sensor-graph"/g) || []).length, 2);
  assert.match(html, /data-live-series="movement"/);
  assert.match(html, /data-live-series="vitals"/);
  assert.doesNotMatch(html, /stroke-width="2"/);
  assert.doesNotMatch(html, /<circle /);
});

test('neighbouring points are joined and a lone point is a dot', () => {
  const data = { channels: { move: withTail([4, 5, null, 3]), still: withTail([]) }, valid: null };
  const html = renderLiveTrend(spec(), data, ui);
  assert.match(html, /<path d="M[\d.]+,[\d.]+ L[\d.]+,[\d.]+"/);
  assert.equal((html.match(/<circle /g) || []).length, 1);
});

test('an invalid point is a gap', () => {
  const values = scaledValues(spec({ channels: ['move'] }), { channels: { move: withTail([1, 2, 3]) }, valid: withTail([true, false, true]) });
  assert.deepEqual(values[0].slice(-3), [1, null, 3]);
});

test('the axis follows the visible values with headroom, in min_span steps', () => {
  assert.equal(displayMaximum([[18, null]], 5), 25);
  assert.equal(displayMaximum([[null]], 5), 5);
  const html = renderLiveTrend(spec(), { channels: { move: withTail([18]) } }, ui);
  assert.match(html, />25</);
});

test('a signed series is centred on zero and a scale converts for display', () => {
  const position = spec({ key: 'position', channels: ['x'], axis: 'signed', min_span: 100, unit_label: ' mm' });
  const html = renderLiveTrend(position, { channels: { x: withTail([240, -50]) } }, ui);
  assert.match(html, />\+300 mm</);
  assert.match(html, />−300 mm</);
  const percent = spec({ key: 'mental', channels: ['attention'], scale: 100, min_span: 5, unit_label: '%' });
  assert.deepEqual(scaledValues(percent, { channels: { attention: withTail([0.42]) } })[0].at(-1), 42);
});

test('legend labels come from the channel key prefix', () => {
  const seen = [];
  const recordingUi = { ...ui, t: (key, fallback) => { seen.push(key); return fallback; } };
  renderLiveTrends({ manifest: manifest(), live: null }, recordingUi);
  assert.ok(seen.includes('fixture.channel.move'));
  assert.ok(seen.includes('dashboard.live.note'));
});

test('short or missing lists are padded at the start, never stretched', () => {
  const values = scaledValues(spec({ channels: ['move'] }), { channels: { move: [7] } });
  assert.equal(values[0].length, POINTS);
  assert.equal(values[0].at(-1), 7);
  assert.equal(values[0][0], null);
});
