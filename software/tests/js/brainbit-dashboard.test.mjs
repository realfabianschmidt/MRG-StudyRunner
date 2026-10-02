import assert from 'node:assert/strict';
import test from 'node:test';
import { renderDashboard } from '../../study_runner/plugins/sensors/brainbit/ui/dashboard.js';
import { graphSection, infoTip } from '../../study_runner/apps/ui/scripts/shared/dashboard-graph.js';

const ui = {
  escapeHtml: (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;'),
  t: (key, fallback) => fallback,
  formatEnabled: String, statusLabel: String, fieldLabel: (name) => name,
  formatValue: (value) => String(value ?? '-'), formatSensorChannels: () => '-',
  formatHealthValue: (value) => value || '-', formatTimestampAge: () => '-',
  formatObjectBrief: () => '-', renderRuntimeButtons: () => '',

  graphSection, infoTip,
};
const point = (at, validity = 'valid', connection_id = 'new') => ({
  at, received_at: at, validity, connection_id, values: { alpha: .2 },
});

// The band and index graphs are the core's live view (tests/js/live-trend.test.mjs).

test('a connected band shows only notes the connection panel does not', () => {
  const html = renderDashboard({ plugin: { connection_state: 'connected', low_battery: true, health: { raw_eeg: 'receiving' },
    latest: { last_message: 'Reconnecting to BrainBit', status_detail_key: 'obsolete.error' } } }, ui);
  assert.doesNotMatch(html, /Reconnecting to BrainBit|obsolete.error/);
  assert.match(html, /low battery/);
  assert.match(html, /<details>/);
});

test('the tile draws no switch, device list or step buttons of its own', () => {
  const html = renderDashboard({ plugin: { key: 'brainbit', connection_state: 'connected', can_restart: true, latest: {} } }, ui);
  assert.doesNotMatch(html, /data-runtime-toggle|data-runtime-switch|data-plugin-admin-action|<select/);
});
