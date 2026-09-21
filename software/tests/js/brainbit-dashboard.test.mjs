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
  const html = renderTrend('bands', {connection_id: 'expired-preview', preview: {bands:[point(1, 'valid', 'expired-preview'),point(2, 'valid', 'expired-preview')]}}, ui, 100);
  // No point falls inside the visible window, so the axis floors at its minimum.
  assert.match(html, /5%/);
  assert.match(html, /−60 s/);
  assert.match(html, /Age: 98 s/);
  assert.doesNotMatch(html, /<path d="M[\d., ]+[ML]/);
});

test('a graph with sustained low values zooms in instead of sitting flat at the bottom', () => {
  const highActivity = (at) => ({ at, received_at: at, validity: 'valid', connection_id: 'zoom', values: { alpha: .08 } });
  const points = Array.from({ length: 12 }, (_, index) => highActivity(index));
  const html = renderTrend('bands', { connection_id: 'zoom', preview: { bands: points } }, ui, 11);
  // 0.08 peak plus headroom rounds up to the next five-percent step.
  assert.match(html, /10%/);
});

test('the full visible window sets the scale without clipping older peaks', () => {
  const samples = [point(1), point(40), point(41)];
  samples[0].values.alpha = .8;
  const html = renderTrend('bands', {connection_id:'new', preview:{bands:samples}}, ui, 41);
  assert.match(html, /95%/);
  const path = html.match(/<path d="([ML0-9., ]+)" fill="none" stroke="#008060"/)[1];
  assert.doesNotMatch(path, /,18\.00/); // Peak has headroom; it was not clamped.
});

test('invalid and previous-connection peaks do not influence scaling', () => {
  const samples = [point(1, 'uncertain'), point(2, 'valid', 'old'), point(3)];
  samples[0].values.alpha = 100;
  samples[1].values.alpha = 100;
  const html = renderTrend('bands', {connection_id:'new', preview:{bands:samples}}, ui, 3);
  assert.match(html, /25%/);
  const next = renderTrend('bands', {connection_id:'next', preview:{bands:[]}}, ui, 3);
  assert.match(next, /5%/);
});

test('current connection message replaces an obsolete reconnect error', () => {
  const html = renderDashboard({plugin:{connection_state:'connected', health:{raw_eeg:'receiving'},
    latest:{last_message:'Reconnecting to BrainBit',status_detail_key:'obsolete.error'}}}, ui);
  assert.doesNotMatch(html, /Reconnecting to BrainBit|obsolete.error/);
  assert.match(html, /EEG is arriving/);
  assert.match(html, /<details>/);
});
