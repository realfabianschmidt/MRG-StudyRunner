// Card registration and defaults come from the shared extension catalog.
import { getJson } from '../shared/api-client.js';
import { loadPluginCatalog, getPluginCatalog, getPluginCatalogGeneration, loadPluginUiExtension } from '../shared/plugin-catalog.js';

export const CARDS = {};
export const CARD_TYPES = [];
const definitions = new Map();
const defaultsByType = new Map();
const pending = new Map();
let generation = -1;

function refreshDefinitions() {
  const next = getPluginCatalogGeneration();
  if (generation === next) return;
  generation = next;
  for (const key of Object.keys(CARDS)) delete CARDS[key];
  CARD_TYPES.length = 0;
  definitions.clear();
  defaultsByType.clear();
  pending.clear();
  for (const plugin of getPluginCatalog().plugins) {
    const contract = plugin.capability_config?.card_contract;
    if (!contract) continue;
    for (const type of contract.question_types) {
      if (definitions.has(type)) throw new Error(`Duplicate card type: ${type}`);
      definitions.set(type, { plugin, answerless: contract.answerless_types.includes(type) });
    }
  }
}

export function isAnswerless(type) { return definitions.get(type)?.answerless === true; }

export function hidesSharedPrompt(type) {
  return CARDS[type]?.metaByType?.[type]?.hideSharedPrompt === true;
}

export function assertCardsAvailable(types) {
  const missing = [...new Set(types)].filter(type => !CARDS[type] || !defaultsByType.has(type));
  if (missing.length) throw new Error(`Required cards could not be loaded: ${missing.join(', ')}. Please contact the study supervisor.`);
}

export async function loadCards({ types = null, requiredTypes = types || [], importer, fetchDefaults } = {}) {
  await loadPluginCatalog();
  refreshDefinitions();
  const wanted = types === null ? [...definitions.keys()] : types;
  const plugins = new Map(wanted.map(type => [definitions.get(type)?.plugin.plugin_key, definitions.get(type)?.plugin]));
  await Promise.allSettled([...plugins.values()].filter(Boolean).map(plugin => loadCard(plugin, { importer, fetchDefaults })));
  assertCardsAvailable(requiredTypes);
  return CARDS;
}

async function loadCard(plugin, options) {
  const contract = plugin.capability_config.card_contract;
  if (contract.question_types.every(type => CARDS[type])) return;
  if (pending.has(plugin.plugin_key)) return pending.get(plugin.plugin_key);
  const startedGeneration = generation;
  const load = async () => {
    const [module, response] = await Promise.all([
      loadPluginUiExtension(plugin, 'card', { importer: options.importer, timeoutMs: 6000 }),
      options.fetchDefaults ? options.fetchDefaults(plugin) : getJson(`/api/plugins/${encodeURIComponent(plugin.plugin_key)}/card-defaults`, { timeoutMs: 12000 }),
    ]);
    if (startedGeneration !== generation || startedGeneration !== getPluginCatalogGeneration()) return;
    for (const name of ['renderStudy', 'renderEditor', 'collectConfig', 'collectAnswer', 'configureCard']) {
      if (typeof module?.[name] !== 'function') throw new Error(`${plugin.plugin_key}: missing ${name}`);
    }
    const defaults = response?.defaults;
    for (const type of contract.question_types) {
      if (!defaults?.[type] || defaults[type].type !== type || !module.metaByType?.[type]) {
        throw new Error(`${plugin.plugin_key}: invalid defaults or presentation for ${type}`);
      }
    }
    module.configureCard(structuredClone(defaults));
    for (const type of contract.question_types) {
      CARDS[type] = module;
      defaultsByType.set(type, structuredClone(defaults[type]));
    }
    CARD_TYPES.length = 0;
    for (const [type] of definitions) {
      if (CARDS[type]) CARD_TYPES.push({ type, module: CARDS[type], overrideMeta: CARDS[type].metaByType[type] });
    }
  };
  const promise = load().catch(error => { console.warn(`[cards] ${plugin.plugin_key} unavailable:`, error); throw error; })
    .finally(() => { if (generation === startedGeneration) pending.delete(plugin.plugin_key); });
  pending.set(plugin.plugin_key, promise);
  return promise;
}

export function defaultFor(type) {
  assertCardsAvailable([type]);
  return structuredClone(defaultsByType.get(type));
}
