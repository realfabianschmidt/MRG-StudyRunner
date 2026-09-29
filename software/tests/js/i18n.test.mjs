import assert from 'node:assert/strict';
import test from 'node:test';
import { preloadLanguage, setLanguage, t, withLanguage } from '../../study_runner/apps/ui/scripts/shared/i18n.js';

test('participant language does not persist and preview translation leaves admin language intact', async () => {
  const writes = [];
  globalThis.window = { localStorage: { setItem: (...args) => writes.push(args) } };
  globalThis.document = {
    documentElement: { lang: 'en' },
    querySelectorAll: () => [],
    dispatchEvent: () => {},
  };
  globalThis.fetch = async (url) => ({
    ok: true,
    json: async () => ({ greeting: url.endsWith('/de.json') ? 'Hallo' : 'Hello' }),
  });
  await setLanguage('en', { persist: false });
  await preloadLanguage('de');
  assert.equal(t('greeting'), 'Hello');
  assert.equal(withLanguage('de', () => t('greeting')), 'Hallo');
  assert.equal(t('greeting'), 'Hello');
  assert.deepEqual(writes, []);
  await setLanguage('de', { persist: false });
  assert.equal(document.documentElement.lang, 'de');
  assert.deepEqual(writes, []);
});
