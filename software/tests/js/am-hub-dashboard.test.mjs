import assert from 'node:assert/strict';
import test from 'node:test';
import { renderTrend, renderDashboard } from '../../study_runner/plugins/sensors/am_hub/ui/dashboard.js';
import { graphSection, infoTip } from '../../study_runner/apps/ui/scripts/shared/dashboard-graph.js';

const ui = {
  escapeHtml: (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;'),
  t: (key, fallback) => fallback,
  formatEnabled: String, statusLabel: String, fieldLabel: (name) => name,
  formatValue: (value, unit = '') => (value === null || value === undefined ? '-' : `${value}${unit}`),
  formatBoolean: (value) => String(!!value),
  formatSensorChannels: () => '-', formatTimestampAge: () => '-',

  graphSection, infoTip,
};
const point = (at, values, validity = 'valid') => ({ at, received_at: at, values, validity });

test('an unsigned trend (movement) never dips below its minimum step', () => {
  const html = renderTrend('movement', { preview: { movement: [] } }, ui, 10);
  assert.match(html, />5</); // MIN step for movement is 5, no unit suffix.
});

test('an unsigned trend zooms to the peak of the visible window plus headroom', () => {
  const points = [point(1, { moveEnergy: 18, staticEnergy: 2 })];
  const html = renderTrend('movement', { preview: { movement: points } }, ui, 1);
  // 18 * 1.15 = 20.7, rounded up to the next multiple of 5 -> 25.
  assert.match(html, />25</);
});

test('a signed trend (position) is centered on zero with a plus/minus label', () => {
  const points = [point(1, { personX: 240, personY: -50 })];
  const html = renderTrend('position', { preview: { position: points } }, ui, 1);
  // max(|240|,|50|) * 1.15 = 276, rounded up to the next 100mm step -> 300.
  assert.match(html, />\+300 mm</);
  assert.match(html, />−300 mm</);
});

test('an invalid point is skipped and breaks the line', () => {
  const points = [point(1, { moveEnergy: 10 }), point(2, { moveEnergy: 10 }, 'uncertain'), point(3, { moveEnergy: 10 })];
  const html = renderTrend('movement', { preview: { movement: points } }, ui, 3);
  const d = html.match(/<path d="([ML0-9., ]+)" fill="none" stroke="#b2182b"/)[1];
  assert.equal((d.match(/M/g) || []).length, 2);
});

test('dashboard renders the trend graphs and details without its own switch', () => {
  const html = renderDashboard({
    plugin: {
      status: 'connected', enabled: true, can_start: false, can_stop: true, can_restart: true,
      base_url: 'http://hub:8000', last_message: 'AM Hub sample received.',
      latest: { presence: 1, personX: 10, personY: -5, moveEnergy: 12 },
      preview: { movement: [], position: [] },
    },
  }, ui);
  assert.doesNotMatch(html, /data-runtime-toggle|data-runtime-switch/);
  assert.match(html, /No person detected by any sensor\./);
  assert.match(html, /Movement &amp; presence energy/);
  assert.match(html, /Position \(relative to the sensor\)/);
  assert.match(html, /<details>/);
  assert.match(html, /http:\/\/hub:8000/);
});

test('the person line names every source that sees someone', () => {
  const html = renderDashboard({
    plugin: { status: 'connected', person: { detected: true, sources: ['position', 'vitals'] }, latest: {}, preview: {} },
  }, ui);
  assert.match(html, /<strong>Person detected<\/strong> – via radar position · heart \/ breathing/);
});

test('while the hub is not connected, the tile says why instead of guessing a person', () => {
  const html = renderDashboard({
    plugin: { status: 'stale', last_message: 'Connection to the AM Hub lost (no data from the hub for 2.1 s); reconnecting.',
      person: { detected: false, sources: [] }, latest: { heartRate: 70 }, preview: {} },
  }, ui);
  assert.match(html, /Connection to the AM Hub lost/);
  assert.doesNotMatch(html, /No person detected/);
  assert.doesNotMatch(html, /<strong>70 BPM<\/strong>/);
});

test('each board shows whether it delivers, and at what rate', () => {
  const html = renderDashboard({
    plugin: { status: 'no_presence', latest: {}, preview: {},
      boards: { radar: { live: true, rate_hz: 9.5, age_s: 0.1 }, bio: { live: false, rate_hz: 0, age_s: 12 } } },
  }, ui);
  assert.match(html, /Radar 9\.5 Hz/);
  assert.match(html, /Vital signs silent 12 s/);
});

test('every value the hub sent is listed, unknown topics marked', () => {
  const html = renderDashboard({
    plugin: { status: 'connected', latest: {}, preview: {},
      topics: { '/sensor/heartBpm': { value: 72, age_s: 0.2 }, '/sensor/rssiRadar': { value: -61, age_s: 1.1 } },
      unknown_topics: ['/sensor/rssiRadar'] },
  }, ui);
  assert.match(html, /All values from the hub \(2\)/);
  assert.match(html, /<dt>\/sensor\/rssiRadar \*<\/dt><dd>-61/);
  assert.match(html, /<dt>\/sensor\/heartBpm<\/dt><dd>72/);
});

test('a hub with WiFi power saving on gets a warning with the fix', () => {
  const on = renderDashboard({ plugin: { status: 'connected', latest: {}, preview: {}, hub_host: { wifi_power_save: 'on' } } }, ui);
  assert.match(on, /WiFi power saving is on at the AM Hub/);
  assert.match(on, /install-network-helpers\.sh/);
  const off = renderDashboard({ plugin: { status: 'connected', latest: {}, preview: {}, hub_host: { wifi_power_save: 'off' } } }, ui);
  assert.doesNotMatch(off, /WiFi power saving/);
});
