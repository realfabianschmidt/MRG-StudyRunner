import fs from 'node:fs/promises';
import { rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
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

// A card may split its browser code into several modules that import each
// other relatively. Each card folder is copied once into a temp directory
// with the '/static/' imports pointed at the real UI files, and loaded from
// there, so relative imports resolve exactly as they do in the browser.
const cardCopies = new Map();
const copyRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'study-runner-cards-'));
process.on('exit', () => {
  try { rmSync(copyRoot, { recursive: true, force: true }); } catch { /* best effort */ }
});

async function cardFolderCopy(key) {
  if (cardCopies.has(key)) return cardCopies.get(key);
  const source = new URL(`study_runner/plugins/cards/${key}/`, software);
  const target = path.join(copyRoot, key);
  await fs.mkdir(target, { recursive: true });
  for (const name of await fs.readdir(source)) {
    if (!name.endsWith('.js')) continue;
    const text = await fs.readFile(new URL(name, source), 'utf8');
    await fs.writeFile(path.join(target, name), text.replaceAll("'/static/", `'${ui.href}`));
  }
  cardCopies.set(key, target);
  return target;
}

export async function importCard(url) {
  const key = url.split('/')[3];
  const folder = await cardFolderCopy(key);
  return import(pathToFileURL(path.join(folder, 'card.js')).href);
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
