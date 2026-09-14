import assert from 'node:assert/strict';
import {
  configurePluginCatalog,
  getPluginCatalog,
  getPluginUiExtension,
  isPluginVisible,
  loadPluginStyles,
  loadPluginUiExtensions,
  PLUGIN_UI_SURFACES,
  pluginByKey,
  pluginUiIcon,
  visiblePluginsWithCapability,
} from '../../study_runner/apps/ui/scripts/shared/plugin-catalog.js';

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

const styledPlugin = {
  plugin_key: 'styled_card',
  status: 'valid',
  ui: { assets: ['card.js', 'card.css'] },
};
configurePluginCatalog({ api_version: 5, plugins: [styledPlugin] });
let stylesheetLoads = 0;
let finishStylesheet;
const stylesheetLoader = () => {
  stylesheetLoads += 1;
  return new Promise(resolve => { finishStylesheet = resolve; });
};
const firstStylesheetLoad = loadPluginStyles(styledPlugin, { loader: stylesheetLoader });
const concurrentStylesheetLoad = loadPluginStyles(styledPlugin, { loader: stylesheetLoader });
while (typeof finishStylesheet !== 'function') await new Promise(resolve => setImmediate(resolve));
assert.equal(stylesheetLoads, 1, 'concurrent stylesheet requests share one load');
finishStylesheet();
await Promise.all([firstStylesheetLoad, concurrentStylesheetLoad]);

configurePluginCatalog({ api_version: 5, plugins: [styledPlugin] });
let retryLoads = 0;
await assert.rejects(
  loadPluginStyles(styledPlugin, {
    loader: () => {
      retryLoads += 1;
      return new Promise(() => {});
    },
    timeoutMs: 10,
  }),
  /stylesheet timed out/,
);
await loadPluginStyles(styledPlugin, {
  loader: async () => { retryLoads += 1; },
  timeoutMs: 10,
});
assert.equal(retryLoads, 2, 'a failed stylesheet can be retried');

configurePluginCatalog({ api_version: 5, plugins: [styledPlugin] });
let finishStaleLoad;
const staleLoad = loadPluginStyles(styledPlugin, {
  loader: () => new Promise(resolve => { finishStaleLoad = resolve; }),
});
while (typeof finishStaleLoad !== 'function') await new Promise(resolve => setImmediate(resolve));
configurePluginCatalog({ api_version: 5, plugins: [] });
finishStaleLoad();
await assert.rejects(staleLoad, /catalog changed/);

const styleNodes = [];
const styleDocument = {
  createElement: () => ({
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    remove() {
      const index = styleNodes.indexOf(this);
      if (index >= 0) styleNodes.splice(index, 1);
    },
  }),
  head: {
    querySelectorAll: () => styleNodes,
    insertBefore(node, successor) {
      const index = successor ? styleNodes.indexOf(successor) : -1;
      if (index >= 0) styleNodes.splice(index, 0, node);
      else styleNodes.push(node);
      queueMicrotask(() => node.onload());
    },
  },
};
const earlyStyle = { plugin_key: 'early', status: 'valid', ui: { order: 1, assets: ['card.css'] } };
const lateStyle = { plugin_key: 'late', status: 'valid', ui: { order: 2, assets: ['card.css'] } };
configurePluginCatalog({ api_version: 5, plugins: [lateStyle, earlyStyle] });
await Promise.all([
  loadPluginStyles(lateStyle, { documentRef: styleDocument }),
  loadPluginStyles(earlyStyle, { documentRef: styleDocument }),
]);
assert.deepEqual(
  styleNodes.map(node => node.href),
  [
    '/api/plugins/early/assets/card.css',
    '/api/plugins/late/assets/card.css',
  ],
  'stylesheet order follows catalog order even when loads start out of order',
);
