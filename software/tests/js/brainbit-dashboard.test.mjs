import assert from 'node:assert/strict';
import test from 'node:test';
import { renderTrend, renderDashboard } from '../../study_runner/plugins/sensors/brainbit/ui/dashboard.js';

const ui = {
  escapeHtml: (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;'),
  t: (key, fallback) => fallback,
  formatEnabled: String, statusLabel: String, fieldLabel: (name) => name,
  formatValue: (value) => String(value ?? '-'), formatSensorChannels: () => '-',
  formatHealthValue: (value) => value || '-', formatTimestampAge: () => '-',
  formatObjectBrief: () => '-', renderRuntimeButtons: () => '',
};
const point = (at, validity = 'valid', connection_id = 'new') => ({
  at, received_at: at, validity, connection_id, values: { alpha: .2 },
});

test('trend breaks at uncertain values and never joins another connection', () => {
  const html = renderTrend('bands', { connection_id: 'new', preview: { bands: [
    point(10), point(11), point(12, 'uncertain'), point(13), point(14, 'valid', 'old'),
  ] } }, ui, 14);
  const d = html.match(/<path d="([ML0-9., ]+)" fill="none" stroke="#008060"/)[1];
  assert.equal((d.match(/M/g) || []).length, 2);
  assert.equal((d.match(/L/g) || []).length, 1);
});

test('expired preview contains no signal path and includes axes and units', () => {
  const html = renderTrend('bands', {connection_id: 'new', preview: {bands:[point(1),point(2)]}}, ui, 100);
  assert.match(html, /100%/);
  assert.match(html, /−60 s/);
  assert.match(html, /Age: 98 s/);
  assert.doesNotMatch(html, /<path d="M[\d., ]+[ML]/);
});

test('current connection message replaces an obsolete reconnect error', () => {
  const html = renderDashboard({plugin:{connection_state:'connected', health:{raw_eeg:'receiving'},
    latest:{last_message:'Reconnecting to BrainBit',status_detail_key:'obsolete.error'}}}, ui);
  assert.doesNotMatch(html, /Reconnecting to BrainBit|obsolete.error/);
  assert.match(html, /EEG is arriving/);
  assert.match(html, /<details>/);
});
