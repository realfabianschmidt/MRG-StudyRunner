import assert from 'node:assert/strict';
import test from 'node:test';

import { ParticipantPluginExtensionManager } from '../../study_runner/web/scripts/lib/participant-plugin-extensions.js';

function createManager({ factory, enabled = () => true, warnings = [] }) {
  const plugin = { plugin_key: 'fixture_sensor', ui: { extensions: { participant: 'ui/participant.js' } } };
  const module = { createParticipantExtension: factory };
  return new ParticipantPluginExtensionManager({
    getPlugins: () => [plugin],
    isEnabled: enabled,
    loadExtensions: async () => new Map([[plugin.plugin_key, module]]),
    getExtensionModule: () => module,
    createContext: () => ({ fixture: true }),
    onWarning: (warning) => warnings.push(warning),
  });
}

test('participant extension lifecycle is generic and submit retry restarts the monitor', async () => {
  const calls = [];
  let cleanupCount = 0;
  let reportedStatus = null;
  const manager = createManager({
    factory: (context) => {
      assert.equal(context.fixture, true);
      assert.equal(context.plugin.plugin_key, 'fixture_sensor');
      reportedStatus = context.reportStatus;
      return {
        onRuntimeChange: ({ enabled }) => calls.push(`runtime:${enabled}`),
        startPrestudyMonitor: ({ reason }) => calls.push(`monitor-start:${reason}`),
        stopPrestudyMonitor: ({ reason }) => calls.push(`monitor-stop:${reason}`),
        startStimulus: ({ stimulus }) => {
          calls.push(`stimulus:${stimulus.index}`);
          return () => { cleanupCount += 1; };
        },
        beforeSubmit: () => calls.push('before-submit'),
        onSubmitFailed: () => calls.push('submit-failed'),
        getHeartbeatStatus: () => ({ samples: 12 }),
        dispose: ({ reason }) => calls.push(`dispose:${reason}`),
      };
    },
  });

  await manager.sync({ reason: 'config' });
  manager.startPrestudyMonitors({ reason: 'ready' });
  manager.startPrestudyMonitors({ reason: 'duplicate' });
  const cleanup = manager.startStimulus({ stimulus: { index: 4 } });
  await Promise.resolve();
  cleanup();
  reportedStatus({ state: 'active', quality: 'good' });
  manager.beforeSubmit({});
  manager.onSubmitFailed({});
  await Promise.resolve();

  assert.equal(cleanupCount, 1);
  assert.deepEqual(calls.slice(0, 4), [
    'runtime:true',
    'monitor-start:ready',
    'stimulus:4',
    'monitor-stop:submit',
  ]);
  assert.ok(calls.includes('before-submit'));
  assert.ok(calls.includes('submit-failed'));
  assert.ok(calls.includes('monitor-start:submit_failed'));
  assert.deepEqual(manager.heartbeatStatus().fixture_sensor, {
    enabled: true,
    state: 'active',
    last_error: '',
    quality: 'good',
    samples: 12,
  });

  await manager.dispose('test_complete');
  assert.ok(calls.includes('dispose:test_complete'));
});

test('a broken optional extension is isolated from timer and navigation callers', async () => {
  const warnings = [];
  const manager = createManager({
    warnings,
    factory: () => ({
      onRuntimeChange() { throw new Error('runtime exploded'); },
      startPrestudyMonitor() { throw new Error('camera denied'); },
      startStimulus() { return Promise.reject(new Error('capture failed')); },
      beforeSubmit() { throw new Error('stop failed'); },
      getHeartbeatStatus() { throw new Error('status failed'); },
    }),
  });

  await manager.sync({ reason: 'runtime' });
  manager.startPrestudyMonitors({ reason: 'ready' });
  const cleanup = manager.startStimulus({ stimulus: { index: 0 } });
  manager.beforeSubmit({});
  assert.equal(typeof cleanup, 'function');
  cleanup();
  await new Promise((resolve) => setTimeout(resolve, 0));

  const status = manager.heartbeatStatus().fixture_sensor;
  assert.equal(status.state, 'warning');
  assert.ok(status.last_error);
  assert.ok(warnings.length >= 4);
});

test('disabling an active plugin stops and disposes only its extension', async () => {
  let active = true;
  const calls = [];
  const manager = createManager({
    enabled: () => active,
    factory: () => ({
      startPrestudyMonitor: () => calls.push('start'),
      stopPrestudyMonitor: () => calls.push('stop'),
      dispose: () => calls.push('dispose'),
    }),
  });

  await manager.sync();
  manager.startPrestudyMonitors();
  active = false;
  await manager.sync({ reason: 'runtime' });

  assert.deepEqual(calls, ['start', 'stop', 'dispose']);
  assert.equal(manager.heartbeatStatus().fixture_sensor.enabled, false);
  assert.equal(manager.heartbeatStatus().fixture_sensor.state, 'disabled');
});
