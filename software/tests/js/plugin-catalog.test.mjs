import assert from 'node:assert/strict';
import {
  configurePluginCatalog,
  getPluginCatalog,
  getPluginUiExtension,
  isPluginVisible,
  loadPluginUiExtensions,
  PLUGIN_UI_SURFACES,
  pluginByKey,
  pluginUiIcon,
  visiblePluginsWithCapability,
} from '../../study_runner/frontend/scripts/shared/plugin-catalog.js';

configurePluginCatalog({
  api_version: 3,
  plugins: [
    {
      plugin_key: 'hidden_sensor',
      status: 'valid',
      capabilities: ['study_sensor', 'lsl_stream_provider'],
      ui: { order: 20, visibility: { dashboard: false, study_settings: true } },
    },
    {
      plugin_key: 'future_destination',
      status: 'valid',
      capabilities: ['upload_destination'],
      ui: { order: 5, visibility: { destination_settings: true } },
    },
    {
      plugin_key: 'broken_plugin',
      status: 'invalid',
      capabilities: ['study_sensor'],
    },
  ],
  invalid_plugins: [{ plugin_key: 'broken_plugin', errors: ['bad manifest'] }],
});

assert.deepEqual(
  getPluginCatalog().plugins.map((plugin) => plugin.plugin_key),
  ['future_destination', 'hidden_sensor'],
);
assert.equal(getPluginCatalog().invalid_plugins.length, 1);
assert.equal(pluginByKey('broken_plugin'), null);
assert.equal(
  isPluginVisible(pluginByKey('hidden_sensor'), PLUGIN_UI_SURFACES.DASHBOARD),
  false,
);
assert.deepEqual(
  visiblePluginsWithCapability('study_sensor', PLUGIN_UI_SURFACES.STUDY_SETTINGS)
    .map((plugin) => plugin.plugin_key),
  ['hidden_sensor'],
);
assert.equal(pluginUiIcon(pluginByKey('future_destination')), 'iconoir-cloud-upload');
assert.equal(pluginUiIcon(pluginByKey('hidden_sensor')), 'iconoir-activity');

let importedUrl = '';
configurePluginCatalog({
  api_version: 3,
  plugins: [{
    plugin_key: 'new_sensor_without_core_changes',
    status: 'valid',
    ui: { extensions: { dashboard: 'ui/dashboard.js' } },
  }],
});
await loadPluginUiExtensions('dashboard', {
  importer: async (url) => {
    importedUrl = url;
    return { renderDashboard: () => '<p>ready</p>' };
  },
  timeoutMs: 50,
});
assert.equal(
  importedUrl,
  '/api/plugins/new_sensor_without_core_changes/assets/ui/dashboard.js',
);
assert.equal(
  getPluginUiExtension('new_sensor_without_core_changes', 'dashboard').renderDashboard(),
  '<p>ready</p>',
);

configurePluginCatalog({
  api_version: 3,
  plugins: [{
    plugin_key: 'slow_optional_view',
    status: 'valid',
    ui: { extensions: { dashboard: 'ui/dashboard.js' } },
  }],
});
const startedAt = Date.now();
let slowImports = 0;
await loadPluginUiExtensions('dashboard', {
  importer: () => {
    slowImports += 1;
    return new Promise(() => {});
  },
  timeoutMs: 15,
});
assert.ok(Date.now() - startedAt < 500, 'optional UI extension load must be bounded');
assert.equal(getPluginUiExtension('slow_optional_view', 'dashboard'), null);
await loadPluginUiExtensions('dashboard', {
  importer: () => {
    slowImports += 1;
    return new Promise(() => {});
  },
  timeoutMs: 15,
});
assert.equal(slowImports, 1, 'failed optional extensions stay on fallback until catalog reload');
