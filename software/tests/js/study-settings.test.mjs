import assert from 'node:assert/strict';
import { normalizeStudySettings } from '../../study_runner/web/scripts/lib/study-settings.js';
import { configurePluginCatalog } from '../../study_runner/web/scripts/lib/plugin-catalog.js';

configurePluginCatalog({
  api_version: 3,
  plugins: [
    ['brainbit', ['study_sensor'], { study_sensor: { default_enabled: true, default_required: true } }],
    ['mini_radar', ['study_sensor'], { study_sensor: { default_enabled: true, default_required: true } }],
    ['camera_emotion', ['study_sensor'], { study_sensor: { default_enabled: false, default_required: true } }],
    ['future_sensor', ['study_sensor'], { study_sensor: { default_enabled: true, default_required: false } }],
    ['notion', ['upload_destination'], { upload_destination: {
      default_enabled: false,
      legacy: {
        enabled_field: 'notion_enabled',
        settings: { database_id: 'notion_database_id' },
      },
    } }],
    ['nextcloud', ['upload_destination'], { upload_destination: {
      default_enabled: false,
      legacy: {
        enabled_field: 'nextcloud_enabled',
        settings: { share_link: 'nextcloud_share_link' },
      },
    } }],
    ['fixture_export', ['upload_destination'], { upload_destination: { default_enabled: true } }],
  ].map(([plugin_key, capabilities, capability_config]) => ({
    plugin_key,
    status: 'valid',
    capabilities,
    capability_config,
  })),
});

const normalized = normalizeStudySettings({
  sensors_enabled: true,
  sensors: { brainbit: false, mini_radar: true, camera_emotion: false },
  notion_enabled: true,
  notion_database_id: 'new-db',
  nextcloud_enabled: true,
  nextcloud_share_link: 'https://cloud.example/s/token',
  plugins: {
    brainbit: { enabled: true, required: true, settings: {} },
    mini_radar: { enabled: false, required: true, settings: {} },
    camera_emotion: { enabled: true, required: true, settings: {} },
    notion: { enabled: false, required: false, settings: { database_id: 'old-db' } },
    nextcloud: { enabled: false, required: false, settings: {} },
    future_sensor: { enabled: true, required: false, settings: { mode: 'fast' } },
  },
});

assert.equal(normalized.plugins.brainbit.enabled, false);
assert.equal(normalized.plugins.mini_radar.enabled, true);
assert.equal(normalized.plugins.camera_emotion.enabled, false);
assert.equal(normalized.plugins.notion.enabled, false);
assert.equal(normalized.plugins.notion.settings.database_id, 'old-db');
assert.equal(normalized.plugins.nextcloud.enabled, false);
assert.equal(normalized.plugins.nextcloud.settings.share_link, undefined);
assert.equal(normalized.plugins.fixture_export.enabled, true);
assert.equal(Object.hasOwn(normalized, 'notion_enabled'), false);
assert.equal(Object.hasOwn(normalized, 'nextcloud_share_link'), false);
assert.deepEqual(normalized.plugins.future_sensor, {
  enabled: true,
  required: false,
  settings: { mode: 'fast' },
});

const catalogDefault = normalizeStudySettings({});
assert.equal(catalogDefault.sensors.future_sensor, true);
assert.deepEqual(catalogDefault.plugins.future_sensor, {
  enabled: true,
  required: false,
  settings: {},
});

const legacyOnly = normalizeStudySettings({
  notion_enabled: true,
  notion_database_id: 'legacy-db',
  nextcloud_enabled: true,
  nextcloud_share_link: 'https://cloud.example/s/legacy',
});
assert.equal(legacyOnly.plugins.notion.enabled, true);
assert.equal(legacyOnly.plugins.notion.settings.database_id, 'legacy-db');
assert.equal(legacyOnly.plugins.nextcloud.enabled, true);
assert.equal(legacyOnly.plugins.nextcloud.settings.share_link, 'https://cloud.example/s/legacy');
