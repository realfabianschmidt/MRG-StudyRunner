import fs from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { configurePluginCatalog } from '../../study_runner/apps/ui/scripts/shared/plugin-catalog.js';
import { loadCards, CARDS } from '../../study_runner/apps/ui/scripts/cards/index.js';

const software = new URL('../../', import.meta.url);
const ui = new URL('study_runner/apps/ui/', software);
const snapshot = JSON.parse(execFileSync('python', ['-B', '-c', `
import json
from study_runner.plugin_framework.registry import get_plugin_catalog_payload
from study_runner.runtime_core.studies.card_extension_bridge import defaults_for_extension
from study_runner.plugin_framework.process_host import reset_process_plugins
catalog = get_plugin_catalog_payload()
try:
    defaults = {p['plugin_key']: defaults_for_extension(p['plugin_key']) for p in catalog['plugins'] if 'card_contract' in p['capabilities']}
    print(json.dumps({'catalog': catalog, 'defaults': defaults}))
finally:
    reset_process_plugins()
`], { cwd: fileURLToPath(software), encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } }));

export async function importCard(url) {
  const key = url.split('/')[3];
  let source = await fs.readFile(new URL(`study_runner/plugins/cards/${key}/card.js`, software), 'utf8');
  source = source.replaceAll("'/static/", `'${ui.href}`);
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

export async function loadShippedCards() {
  configurePluginCatalog(snapshot.catalog);
  await loadCards({
    importer: importCard,
    fetchDefaults: async plugin => ({ defaults: snapshot.defaults[plugin.plugin_key] }),
    stylesheetLoader: async () => {},
  });
  return CARDS;
}

export { snapshot };
