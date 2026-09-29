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
      { key: 'auto_reconnect', role: 'auto_reconnect', label: 'Auto-reconnect', payload_schema: { enabled: { type: 'boolean' } } },
    ] },
  },
};

const plugin = (connection, extra = {}) => ({
  key: 'brainbit', running: true, can_start: true, can_stop: true, can_restart: true, connection, ...extra,
});

function ctaRole(html) {
  const match = html.match(/class="sensor-connection-step is-cta"[^>]*data-connection-action="([a-z_]+)"/);
  return match ? match[1] : null;
}

test('ready to connect without a known band: Search is the call to action and the list asks to search', () => {
  const html = renderSensorConnectionPanel(plugin({ phase: 'idle', next_step: 'scan', candidates: [] }), manifest);
  assert.match(html, />Ready to connect</);
  assert.equal(ctaRole(html), 'scan');
  assert.match(html, /<option value="" selected disabled>Please search for devices<\/option>/);
  assert.match(html, /data-connection-action="measure_signal"[^>]*disabled/);
  assert.match(html, /data-connection-action="initialize"[^>]*disabled/);
  assert.doesNotMatch(html, /Connect</);
});

test('connected with poor contact: measure is the call to action, initialize is still possible', () => {
  const connection = {
    phase: 'connected', streaming: true, next_step: 'measure_signal', ready: false,
    device: { id: 'serial:1', label: 'BrainBit 1' },
    signal: { state: 'poor', channels: ['O1', 'T3'] }, setup: { state: 'needed' },
  };
  const html = renderSensorConnectionPanel(plugin(connection), manifest);
  assert.equal(ctaRole(html), 'measure_signal');
  assert.doesNotMatch(html, /data-connection-action="initialize"[^>]*disabled/);
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

test('a recording session locks every step but the auto-reconnect switch', () => {
  const connection = { phase: 'connected', streaming: true, signal: { state: 'good' }, setup: { state: 'needed' } };
  assert.match(stepBlockReason('initialize', connection, { locked: true }), /Locked/);
  assert.equal(stepBlockReason('auto_reconnect', connection, { locked: true }), '');
  const html = renderSensorConnectionPanel(plugin(connection), manifest, { locked: true });
  assert.equal((html.match(/data-connection-action="[a-z_]+"[^>]*disabled/g) || []).length, 3);
  assert.match(html, /<select[^>]*disabled/);
  assert.match(html, /data-connection-action="auto_reconnect" data-lock-exempt/);
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

test('the list comes first and the four round buttons stay beside it', () => {
  const html = renderSensorConnectionPanel(plugin({ phase: 'idle', next_step: 'scan', candidates: [] }), manifest);
  const steps = html.slice(html.indexOf('sensor-connection-steps'));
  assert.ok(steps.indexOf('<select') < steps.indexOf('sensor-connection-buttons'));
  const group = steps.slice(steps.indexOf('sensor-connection-buttons'));
  assert.deepEqual(
    [...group.matchAll(/data-connection-action="([a-z_]+)"/g)].map((match) => match[1]),
    ['scan', 'measure_signal', 'initialize', 'auto_reconnect'],
  );
  assert.doesNotMatch(html, /btn-primary sensor-connection-step|btn-secondary sensor-connection-step/);
});

test('while starting, everything but the switch waits', () => {
  const html = renderSensorConnectionPanel(plugin({ phase: 'starting' }), manifest);
  assert.match(html, />Starting …</);
  assert.equal((html.match(/data-connection-action="[a-z_]+"[^>]*disabled/g) || []).length, 4);
  assert.match(html, /<select[^>]*disabled/);
  assert.match(html, /data-runtime-toggle="brainbit"[^>]*checked/);
});

test('searching blocks the list and the steps, but not auto-reconnect', () => {
  const html = renderSensorConnectionPanel(plugin({ phase: 'searching' }), manifest);
  assert.match(html, /<select[^>]*disabled/);
  assert.match(html, /data-connection-action="scan"[^>]*disabled/);
  assert.doesNotMatch(html, /data-connection-action="auto_reconnect"[^>]*disabled/);
});

test('reconnecting by itself leaves search and the list usable', () => {
  const connection = { phase: 'reconnecting', device: { id: 'serial:1', label: 'BrainBit 1' },
    auto_reconnect: { enabled: true, active: true } };
  const html = renderSensorConnectionPanel(plugin(connection), manifest);
  assert.doesNotMatch(html, /<select[^>]*disabled/);
  assert.doesNotMatch(html, /data-connection-action="scan"[^>]*disabled/);
  assert.match(html, /Connection lost – reconnecting …/);
});

test('the band used last time is offered, not pre-selected, so choosing it connects', () => {
  const connection = { phase: 'idle', next_step: 'select', device: { id: 'serial:1', label: 'BrainBit 1' },
    candidates: [{ id: 'serial:1', label: 'BrainBit 1', payload: { serial_number: '1' }, note: 'last_used' }] };
  const html = renderSensorConnectionPanel(plugin(connection), manifest);
  assert.match(html, /<option value="" selected disabled>Choose a device<\/option>/);
  assert.match(html, /data-option-key="serial:1" >BrainBit 1 \(last used\)<\/option>/);
  assert.match(html, /sensor-connection-select is-cta/);
  assert.equal(ctaRole(html), null);
});

test('a lost connection names what happened and what to do', () => {
  const connection = { phase: 'failed', detail: 'connection_lost', next_step: 'select',
    candidates: [{ id: 'serial:1', label: 'BrainBit 1', payload: {}, note: 'last_used' }] };
  const parts = connectionStatusText(connection, manifest);
  assert.equal(parts[0], 'Connection lost');
  assert.match(parts[1], /Choose the device again/);
  const nothing = connectionStatusText({ phase: 'idle', detail: 'not_found' }, manifest);
  assert.equal(nothing[0], 'Ready to connect');
  assert.match(nothing[1], /Nothing found/);
});

test('the auto-reconnect switch shows its position and whether it is armed', () => {
  const connected = { phase: 'connected', streaming: true, signal: { state: 'good' }, setup: { state: 'done' } };
  const on = renderSensorConnectionPanel(plugin({ ...connected, auto_reconnect: { enabled: true, active: false } }), manifest);
  assert.match(on, /sensor-connection-toggle is-on"[^>]*aria-pressed="true"/);
  assert.match(on, /takes effect once a device is connected and the study runs/);
  const active = renderSensorConnectionPanel(plugin({ ...connected, auto_reconnect: { enabled: true, active: true } }), manifest);
  assert.match(active, /sensor-connection-toggle is-on is-active"/);
  const off = renderSensorConnectionPanel(plugin({ ...connected, auto_reconnect: { enabled: false, active: false } }), manifest);
  assert.match(off, /sensor-connection-toggle"[^>]*aria-pressed="false"/);
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
