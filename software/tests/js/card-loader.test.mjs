import assert from 'node:assert/strict';
import test from 'node:test';

import { configurePluginCatalog } from '../../study_runner/apps/ui/scripts/shared/plugin-catalog.js';
import { CARD_TYPES, defaultFor, loadCards } from '../../study_runner/apps/ui/scripts/cards/index.js';

function plugin(key, type, order) {
  return {
    plugin_key: key,
    status: 'valid',
    capabilities: ['card_contract'],
    capability_config: {
      card_contract: { version: 1, question_types: [type], answerless_types: [], host_data: [] },
    },
    ui: { order, extensions: { card: 'card.js' }, assets: ['card.js'] },
  };
}

function moduleFor(type) {
  let configured = null;
  return {
    metaByType: { [type]: { type, icon: 'puzzle', label: type, pill: 'pill-text' } },
    configureCard(defaults) { configured = defaults; },
    renderStudy() { return configured ? '<p>ready</p>' : ''; },
    renderEditor() { return ''; },
    collectConfig() { return {}; },
    collectAnswer() { return null; },
  };
}

test('catalog order drives the picker and defaults are fetched once per generation', async () => {
  const later = plugin('later_card', 'later-card', 20);
  const earlier = plugin('earlier_card', 'earlier-card', 10);
  configurePluginCatalog({ plugins: [later, earlier] });
  const modules = { later_card: moduleFor('later-card'), earlier_card: moduleFor('earlier-card') };
  let defaultsCalls = 0;
  const options = {
    importer: async url => modules[url.split('/')[3]],
    fetchDefaults: async item => {
      defaultsCalls += 1;
      const type = item.capability_config.card_contract.question_types[0];
      return { defaults: { [type]: { type, prompt: `${type} default` } } };
    },
  };

  await loadCards(options);
  await loadCards(options);

  assert.deepEqual(CARD_TYPES.map(item => item.type), ['earlier-card', 'later-card']);
  assert.equal(defaultsCalls, 2);
  const first = defaultFor('earlier-card');
  first.prompt = 'mutated';
  assert.equal(defaultFor('earlier-card').prompt, 'earlier-card default');
});

test('card registration waits for CSS and retries a failed stylesheet', async () => {
  const styled = plugin('styled_card', 'styled-card', 1);
  styled.ui.assets.push('card.css');
  configurePluginCatalog({ plugins: [styled] });
  let attempts = 0;
  let releaseStyle;
  const stylesheetLoader = () => {
    attempts += 1;
    if (attempts === 1) return Promise.reject(new Error('synthetic CSS failure'));
    return new Promise(resolve => { releaseStyle = resolve; });
  };
  const options = {
    requiredTypes: ['styled-card'],
    importer: async () => moduleFor('styled-card'),
    fetchDefaults: async () => ({ defaults: { 'styled-card': { type: 'styled-card' } } }),
    stylesheetLoader,
  };

  await assert.rejects(loadCards(options), /Required cards could not be loaded: styled-card/);
  const pendingLoad = loadCards(options);
  while (typeof releaseStyle !== 'function') await new Promise(resolve => setImmediate(resolve));
  assert.throws(() => defaultFor('styled-card'), /Required cards could not be loaded/);
  releaseStyle();
  await pendingLoad;
  assert.equal(defaultFor('styled-card').type, 'styled-card');
  assert.equal(attempts, 2);
});

test('an unrelated broken module is isolated while a required broken module blocks', async () => {
  const healthy = plugin('healthy_card', 'healthy-card', 1);
  const broken = plugin('broken_card', 'broken-card', 2);
  configurePluginCatalog({ plugins: [healthy, broken] });
  const fetchDefaults = async item => {
    const type = item.capability_config.card_contract.question_types[0];
    return { defaults: { [type]: { type, prompt: '' } } };
  };
  const importer = async url => {
    if (url.includes('/broken_card/')) throw new Error('synthetic module failure');
    return moduleFor('healthy-card');
  };

  await loadCards({ requiredTypes: ['healthy-card'], importer, fetchDefaults });
  assert.equal(defaultFor('healthy-card').type, 'healthy-card');
  await assert.rejects(
    loadCards({ requiredTypes: ['broken-card'], importer, fetchDefaults }),
    /Required cards could not be loaded: broken-card/,
  );
});

test('a synthetic card needs only catalog metadata, defaults, and its generic module', async () => {
  const synthetic = plugin('folder_only_card', 'folder-only-card', 1);
  configurePluginCatalog({ plugins: [synthetic] });
  let importedUrl = '';
  await loadCards({
    requiredTypes: ['folder-only-card'],
    importer: async url => { importedUrl = url; return moduleFor('folder-only-card'); },
    fetchDefaults: async () => ({
      defaults: { 'folder-only-card': { type: 'folder-only-card', prompt: 'from Python' } },
    }),
  });
  assert.equal(importedUrl, '/api/plugins/folder_only_card/assets/card.js');
  assert.equal(defaultFor('folder-only-card').prompt, 'from Python');
});
