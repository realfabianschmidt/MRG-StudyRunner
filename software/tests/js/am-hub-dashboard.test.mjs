import assert from 'node:assert/strict';
import test from 'node:test';
import { renderTrend, renderDashboard } from '../../study_runner/plugins/sensors/am_hub/ui/dashboard.js';

const ui = {
  escapeHtml: (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;'),
  t: (key, fallback) => fallback,
  formatEnabled: String, statusLabel: String, fieldLabel: (name) => name,
  formatValue: (value, unit = '') => (value === null || value === undefined ? '-' : `${value}${unit}`),
  formatBoolean: (value) => String(!!value),
  formatSensorChannels: () => '-', formatTimestampAge: () => '-',
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

test('dashboard renders both trend graphs, status row and details', () => {
  const html = renderDashboard({
    plugin: {
      status: 'connected', enabled: true, can_start: false, can_stop: true, can_restart: true,
      base_url: 'http://hub:8000', last_message: 'AM Hub sample received.',
      latest: { presence: 1, personX: 10, personY: -5, moveEnergy: 12 },
      preview: { movement: [], position: [] },
    },
  }, ui);
  assert.match(html, /dashboard-status-row/);
  assert.match(html, /Movement &amp; presence energy/);
  assert.match(html, /Position \(relative to the sensor\)/);
  assert.match(html, /<details>/);
  assert.match(html, /http:\/\/hub:8000/);
});
