import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fieldLabel,
  fieldPlaceholder,
  pluginHelpEntries,
  renderPluginHelpBody,
  renderPluginHelpButton,
} from '../../study_runner/apps/ui/scripts/shared/plugin-help.js';

const documented = {
  plugin_key: 'upload',
  ui: { label: 'Upload', help_key: 'pluginHelp.upload.intro' },
  capability_config: { credentials: { config_field: 'api_key', help_key: 'pluginHelp.upload.key', help: 'Copy the **secret**.', label_key: 'x.keyLabel', placeholder_key: 'x.keyExample' } },
  study_settings_schema: {
    page_id: { type: 'string', help_key: 'pluginHelp.upload.page', help: 'The page ID.', placeholder: 'abc123' },
    hidden: { type: 'string' },
  },
};

test('help button appears only for plugins that declare help', () => {
  assert.match(renderPluginHelpButton(documented), /data-plugin-help="upload"/);
  assert.match(renderPluginHelpButton(documented), /iconoir-help-circle/);
  assert.equal(renderPluginHelpButton({ plugin_key: 'bare', ui: { label: 'Bare' } }), '');
});

test('help lists every documented field with its example, in manifest order', () => {
  const entries = pluginHelpEntries(documented);
  assert.equal(entries.length, 2);
  assert.equal(entries[1].label, 'Page Id');
  assert.equal(entries[1].example, 'abc123');
  const body = renderPluginHelpBody(documented);
  assert.match(body, /<code>abc123<\/code>/);
  assert.doesNotMatch(body, /Hidden/);
});

test('labels and placeholders fall back sensibly without translations', () => {
  assert.equal(fieldLabel('api_key', {}), 'Api Key');
  assert.equal(fieldLabel('api_key', { label: 'Key' }), 'Key');
  assert.equal(fieldPlaceholder({ placeholder: 'e.g. 1' }), 'e.g. 1');
  assert.equal(fieldPlaceholder({}), '');
});
