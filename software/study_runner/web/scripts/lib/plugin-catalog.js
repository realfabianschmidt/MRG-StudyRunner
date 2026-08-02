import { getJson } from '../api-client.js';

let catalog = { api_version: 3, plugins: [], plugins_by_key: {}, invalid_plugins: [] };
let loading = null;
let loaded = false;
let extensionModules = new Map();
let extensionLoads = new Map();
let catalogGeneration = 0;

const EXTENSION_EXPORTS = Object.freeze({
  dashboard: 'renderDashboard',
  participant: 'createParticipantExtension',
});
const EXTENSION_LOAD_TIMEOUT_MS = 2000;

export const PLUGIN_UI_SURFACES = Object.freeze({
  DASHBOARD: 'dashboard',
  SETTINGS_HUB: 'settings_hub',
  STUDY_SETTINGS: 'study_settings',
  DESTINATION_SETTINGS: 'destination_settings',
});

export async function loadPluginCatalog({ force = false } = {}) {
  if (!force && loaded) return catalog;
  if (!force && loading) return loading;
  loading = getJson('/api/plugins/catalog', { timeoutMs: 2500 })
    .then((payload) => configurePluginCatalog(payload))
    .finally(() => { loading = null; });
  return loading;
}

export function configurePluginCatalog(payload) {
  const plugins = Array.isArray(payload?.plugins)
    ? payload.plugins.filter((plugin) => plugin?.status === 'valid' && plugin.plugin_key)
    : [];
  plugins.sort((left, right) => {
    const order = Number(left.ui?.order ?? 1000) - Number(right.ui?.order ?? 1000);
    return order || String(left.plugin_key).localeCompare(String(right.plugin_key));
  });
  catalog = {
    api_version: Number(payload?.api_version || 3),
    plugins,
    plugins_by_key: Object.fromEntries(plugins.map((plugin) => [plugin.plugin_key, plugin])),
    invalid_plugins: Array.isArray(payload?.invalid_plugins) ? payload.invalid_plugins : [],
  };
  loaded = true;
  catalogGeneration += 1;
  extensionModules = new Map();
  extensionLoads = new Map();
  return catalog;
}

export function getPluginCatalog() {
  return catalog;
}

export function pluginsWithCapability(capability) {
  return catalog.plugins.filter((plugin) => (plugin.capabilities || []).includes(capability));
}

/** Return capability providers that opted into one generic UI surface. */
export function visiblePluginsWithCapability(capability, surface) {
  return pluginsWithCapability(capability).filter((plugin) => isPluginVisible(plugin, surface));
}

/**
 * A missing visibility rule means visible for backwards-compatible manifests.
 * Only the manifest decides; plugin keys never do.
 */
export function isPluginVisible(plugin, surface, defaultVisible = true) {
  const visibility = plugin?.ui?.visibility;
  if (visibility === true || visibility === false) return visibility;
  if (!visibility || typeof visibility !== 'object' || Array.isArray(visibility)) {
    return Boolean(defaultVisible);
  }
  if (Object.prototype.hasOwnProperty.call(visibility, surface)) {
    return visibility[surface] !== false;
  }
  return Boolean(defaultVisible);
}

/** Choose presentation by declared metadata/capabilities, never by plugin key. */
export function pluginUiIcon(plugin) {
  const declared = String(plugin?.ui?.icon || '').trim();
  if (declared) return declared;
  const capabilities = new Set(plugin?.capabilities || []);
  if (capabilities.has('upload_destination')) return 'iconoir-cloud-upload';
  if (capabilities.has('recording_worker') || capabilities.has('recording_source')) return 'iconoir-save-action-floppy';
  if (capabilities.has('lsl_stream_provider')) return 'iconoir-activity';
  if (capabilities.has('processing')) return 'iconoir-cpu';
  if (capabilities.has('runtime_control')) return 'iconoir-settings-profiles';
  return 'iconoir-puzzle';
}

export function pluginByKey(pluginKey) {
  return catalog.plugins_by_key[String(pluginKey || '')] || null;
}

/**
 * Load trusted, manifest-declared optional UI modules for one host surface.
 * One broken extension is isolated and leaves that plugin on the generic UI.
 */
export async function loadPluginUiExtensions(surface, options = {}) {
  if (!Object.prototype.hasOwnProperty.call(EXTENSION_EXPORTS, surface)) {
    throw new Error(`Unsupported plugin UI extension surface: ${surface}`);
  }
  const jobs = catalog.plugins.map((plugin) => loadOnePluginUiExtension(plugin, surface, options));
  await Promise.all(jobs);
  return new Map(
    catalog.plugins
      .map((plugin) => [plugin.plugin_key, getPluginUiExtension(plugin, surface)])
      .filter(([, extension]) => extension !== null),
  );
}

export function getPluginUiExtension(pluginOrKey, surface) {
  const pluginKey = typeof pluginOrKey === 'string'
    ? pluginOrKey
    : pluginOrKey?.plugin_key;
  return extensionModules.get(`${pluginKey || ''}:${surface}`) || null;
}

export function pluginUiAssetUrl(pluginOrKey, assetPath) {
  const pluginKey = typeof pluginOrKey === 'string'
    ? pluginOrKey
    : pluginOrKey?.plugin_key;
  const safePath = String(assetPath || '')
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/');
  return `/api/plugins/${encodeURIComponent(pluginKey || '')}/assets/${safePath}`;
}

async function loadOnePluginUiExtension(plugin, surface, options = {}) {
  const assetPath = plugin?.ui?.extensions?.[surface];
  if (!assetPath) return null;
  const cacheKey = `${plugin.plugin_key}:${surface}`;
  const generation = catalogGeneration;
  if (extensionModules.has(cacheKey)) return extensionModules.get(cacheKey);
  if (extensionLoads.has(cacheKey)) return extensionLoads.get(cacheKey);
  const importer = typeof options.importer === 'function'
    ? options.importer
    : (url) => import(url);
  const timeoutMs = Number.isFinite(Number(options.timeoutMs))
    ? Math.max(1, Number(options.timeoutMs))
    : EXTENSION_LOAD_TIMEOUT_MS;

  const promise = new Promise((resolve) => {
    let settled = false;
    const finish = (module) => {
      if (settled) return;
      settled = true;
      globalThis.clearTimeout(timeoutId);
      if (generation === catalogGeneration) extensionLoads.delete(cacheKey);
      resolve(module);
    };
    const timeoutId = globalThis.setTimeout(() => {
      console.warn(`[plugins] ${plugin.plugin_key} ${surface} extension timed out; using generic UI.`);
      if (generation === catalogGeneration) extensionModules.set(cacheKey, null);
      finish(null);
    }, timeoutMs);

    Promise.resolve()
      .then(() => importer(pluginUiAssetUrl(plugin, assetPath)))
      .then((module) => {
        const expectedExport = EXTENSION_EXPORTS[surface];
        if (typeof module?.[expectedExport] !== 'function') {
          throw new Error(`extension must export ${expectedExport}()`);
        }
        if (settled) return;
        if (generation !== catalogGeneration) {
          finish(null);
          return;
        }
        extensionModules.set(cacheKey, module);
        finish(module);
      })
      .catch((error) => {
        if (settled) return;
        console.warn(`[plugins] ${plugin.plugin_key} ${surface} extension unavailable:`, error);
        if (generation === catalogGeneration) extensionModules.set(cacheKey, null);
        finish(null);
      });
  });
  extensionLoads.set(cacheKey, promise);
  return promise;
}
