import assert from 'node:assert/strict';
import test from 'node:test';
import {
  connectionStatusText,
  renderSensorConnectionPanel,
  stepBlockReason,
  studyBarState,
} from '../../study_runner/apps/ui/scripts/admin/sensor-connection-panel.js';

const manifest = {
  plugin_key: 'brainbit',
  capability_config: {
    connection: {
      device_noun_key: 'sensorConnection.noun.headband',
      signal_label_key: 'sensorConnection.signal.electrodeContact',
      setup_label_key: 'sensorConnection.setup.calibration',
      setup_per_participant: true,
    },
    admin_actions: { actions: [
      { key: 'select_device', role: 'select', label: 'Select headband', instances: { presentation: 'select' } },
      { key: 'scan_devices', role: 'scan', label: 'Search' },
      { key: 'check_contact', role: 'measure_signal', label: 'Measure contact' },
      { key: 'calibrate', role: 'initialize', label: 'Initialize' },
    ] },
  },
};

const plugin = (connection, extra = {}) => ({
  key: 'brainbit', running: true, can_start: true, can_stop: true, can_restart: true, connection, ...extra,
});

function ctaRole(html) {
  const match = html.match(/class="btn-primary sensor-connection-step"[^>]*data-connection-action="([a-z_]+)"/);
  return match ? match[1] : null;
}

test('without a known band, Search is the call to action and the list asks to search', () => {
  const html = renderSensorConnectionPanel(plugin({ phase: 'no_device', next_step: 'scan', candidates: [] }), manifest);
  assert.equal(ctaRole(html), 'scan');
  assert.match(html, /<option value="" selected disabled>Please search for devices<\/option>/);
  assert.match(html, /data-connection-action="measure_signal"[^>]*disabled/);
  assert.match(html, /data-connection-action="initialize"[^>]*disabled/);
  assert.doesNotMatch(html, /Connect</);
});

test('connected with poor contact: measure is the call to action, initialize waits', () => {
  const connection = {
    phase: 'connected', streaming: true, next_step: 'measure_signal', ready: false,
    device: { id: 'serial:1', label: 'BrainBit 1' },
    signal: { state: 'poor', channels: ['O1', 'T3'] }, setup: { state: 'needed' },
  };
  const html = renderSensorConnectionPanel(plugin(connection), manifest);
  assert.equal(ctaRole(html), 'measure_signal');
  assert.match(html, /data-connection-action="initialize"[^>]*disabled/);
  assert.match(html, /Electrode contact poor \(O1, T3\)/);
  assert.doesNotMatch(html, /status-pill--ready/);
});

test('good contact: initialize is next, then ready', () => {
  const needed = {
    phase: 'connected', streaming: true, next_step: 'initialize', ready: false,
    signal: { state: 'good' }, setup: { state: 'needed' },
  };
  assert.equal(ctaRole(renderSensorConnectionPanel(plugin(needed), manifest)), 'initialize');
  const ready = { ...needed, next_step: null, ready: true, setup: { state: 'done' } };
  const html = renderSensorConnectionPanel(plugin(ready), manifest);
  assert.equal(ctaRole(html), null);
  assert.match(html, /status-pill--ready/);
  assert.deepEqual(connectionStatusText(ready, manifest), ['Connected', 'Electrode contact good', 'Calibration done']);
});

test('the switch shows the running state, not the ability to stop', () => {
  const off = renderSensorConnectionPanel(plugin({ phase: 'off' }, { running: false }), manifest);
  assert.match(off, /data-runtime-toggle="brainbit"(?![^>]*checked)/);
  const on = renderSensorConnectionPanel(plugin({ phase: 'connecting' }), manifest);
  assert.match(on, /data-runtime-toggle="brainbit"[^>]*checked/);
  const pending = renderSensorConnectionPanel(plugin({ phase: 'connected' }), manifest, { pendingRunning: false });
  assert.match(pending, /data-runtime-toggle="brainbit"(?![^>]*checked)[^>]*disabled/);
});

test('a recording session locks every step', () => {
  const connection = { phase: 'connected', streaming: true, signal: { state: 'good' }, setup: { state: 'needed' } };
  assert.match(stepBlockReason('initialize', connection, { locked: true }), /Locked/);
  const html = renderSensorConnectionPanel(plugin(connection), manifest, { locked: true });
  assert.equal((html.match(/data-connection-action="[a-z_]+"[^>]*disabled/g) || []).length, 3);
});

test('a sensor without steps shows only its status and switch', () => {
  const html = renderSensorConnectionPanel(
    plugin({ phase: 'connected', streaming: true, ready: true, signal: { state: 'unknown', detail: 'no_presence' }, setup: { state: 'not_needed' } }),
    { plugin_key: 'am_hub', capability_config: { connection: { signal_label_key: 'sensorConnection.signal.presence' } } },
  );
  assert.doesNotMatch(html, /sensor-connection-steps/);
  assert.match(html, /no presence/);
  assert.match(html, /status-pill--ready/);
});

test('the connected device is selected even without scan results', () => {
  const connection = { phase: 'connected', streaming: true, device: { id: 'serial:1', label: 'BrainBit 1' }, candidates: [],
    signal: { state: 'good' }, setup: { state: 'done' } };
  const html = renderSensorConnectionPanel(plugin(connection), manifest);
  assert.match(html, /<option value="" data-option-key="serial:1" selected>BrainBit 1 \(connected\)<\/option>/);
});

test('the study bar lists what is missing before Start becomes the call to action', () => {
  const status = {
    study_run_state: { study_id: 'Example Sensors Study', status: 'loaded' },
    sensor_runtime: { effective: { brainbit: true, am_hub: false } },
    study_clients: { single_tablet: { can_start: true } },
    plugins: {
      brainbit: { connection: { ready: false, next_step: 'initialize' } },
      am_hub: { connection: { ready: true } },
    },
  };
  const state = studyBarState(status);
  assert.deepEqual(state.needed, ['brainbit']);
  assert.deepEqual(state.missing, ['brainbit']);
  assert.equal(state.tabletOk, true);
  status.plugins.brainbit.connection.ready = true;
  assert.deepEqual(studyBarState(status).missing, []);
});
