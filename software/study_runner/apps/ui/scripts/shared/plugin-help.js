/**
 * The "(?)" help for a plugin's settings, in plain language.
 *
 * Everything comes from the plugin's manifest as translation keys, so no core
 * code names a plugin:
 *   ui.help_key            introduction shown at the top of the help window
 *   <field>.label_key      field name
 *   <field>.placeholder_key  example value, also used as the input placeholder
 *   <field>.help_key       what the field is, where to find it, typical mistake
 * Fields are the per-study credential, the study settings and the machine
 * settings. Help text may use the small Markdown subset of rich-text.js.
 */
import { t } from './i18n.js';
import { escapeHtml } from './dom-utils.js';
import { createModal } from './modal.js';
import { renderRichText } from './rich-text.js';

let helpModal = null;

function humanize(name) {
  return String(name || '').replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/** Label, example and explanation for every field that declares help. */
export function pluginHelpEntries(plugin) {
  const entries = [];
  const credential = plugin?.capability_config?.credentials || {};
  if (credential.config_field && credential.help_key) {
    entries.push({
      label: fieldLabel(credential.config_field, credential),
      example: fieldPlaceholder(credential),
      help: t(credential.help_key, credential.help || ''),
    });
  }
  const groups = [plugin?.study_settings_schema || plugin?.settings?.study || {}, plugin?.settings?.machine || {}];
  for (const schema of groups) {
    for (const [name, field] of Object.entries(schema || {})) {
      if (!field?.help_key && !field?.help) continue;
      entries.push({ label: fieldLabel(name, field), example: fieldPlaceholder(field), help: field.help_key ? t(field.help_key, field.help || '') : field.help });
    }
  }
  return entries.filter((entry) => entry.help);
}

export function fieldLabel(name, field = {}) {
  return field.label_key ? t(field.label_key, field.label || humanize(name)) : field.label || humanize(name);
}

export function fieldPlaceholder(field = {}) {
  return field.placeholder_key ? t(field.placeholder_key, field.placeholder || '') : field.placeholder || '';
}

export function hasPluginHelp(plugin) {
  return Boolean(plugin?.ui?.help_key) || pluginHelpEntries(plugin).length > 0;
}

/** A round "(?)" button; empty when the plugin declares no help. */
export function renderPluginHelpButton(plugin) {
  if (!hasPluginHelp(plugin)) return '';
  const name = plugin.ui?.label || plugin.plugin_key;
  const label = t('pluginHelp.buttonLabel', 'How do I set up {name}?').replace('{name}', name);
  return `<button type="button" class="btn-icon-only plugin-help-button" data-plugin-help="${escapeHtml(plugin.plugin_key)}"
      title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"><i class="iconoir-help-circle"></i></button>`;
}

export function renderPluginHelpBody(plugin) {
  const intro = plugin?.ui?.help_key ? t(plugin.ui.help_key, '') : '';
  const sections = pluginHelpEntries(plugin).map((entry) => `
    <section class="plugin-help-field">
      <h3>${escapeHtml(entry.label)}</h3>
      ${entry.example ? `<p class="plugin-help-example"><span>${escapeHtml(t('pluginHelp.example', 'Example'))}:</span> <code>${escapeHtml(entry.example)}</code></p>` : ''}
      <div class="plugin-help-text">${renderRichText(entry.help)}</div>
    </section>`).join('');
  return `${intro ? `<div class="plugin-help-intro">${renderRichText(intro)}</div>` : ''}${sections}`;
}

function showHelp(title, html) {
  if (!helpModal) {
    helpModal = createModal({ title: '', variant: 'help', closeLabel: t('settings.close', 'Close') });
  }
  helpModal.setTitle(title);
  helpModal.body.innerHTML = html;
  helpModal.open();
}

export function openPluginHelp(plugin) {
  if (!plugin) return;
  showHelp(
    t('pluginHelp.title', 'Setting up {name}').replace('{name}', plugin.ui?.label || plugin.plugin_key),
    renderPluginHelpBody(plugin),
  );
}

/**
 * The same help window for any explanation a plugin wants to give, with
 * sources. `sections` is `[{ heading, text, references: [{ label, url }] }]`.
 * Text uses the rich-text subset; links are allowed only in `references`,
 * which come from the plugin's own code (never from study text), are escaped,
 * and must be http(s).
 */
export function renderHelpSections({ intro = '', sections = [] } = {}) {
  const body = sections.map((section) => `
    <section class="plugin-help-field">
      <h3>${escapeHtml(section.heading || '')}</h3>
      <div class="plugin-help-text">${renderRichText(section.text || '')}</div>
      ${renderReferences(section.references)}
    </section>`).join('');
  return `${intro ? `<div class="plugin-help-intro">${renderRichText(intro)}</div>` : ''}${body}`;
}

export function openHelpModal({ title = '', intro = '', sections = [] } = {}) {
  showHelp(title, renderHelpSections({ intro, sections }));
}

function renderReferences(references) {
  const links = (Array.isArray(references) ? references : [])
    .filter((reference) => /^https?:\/\//i.test(String(reference?.url || '')))
    .map((reference) => `
      <li><a href="${escapeHtml(reference.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(reference.label || reference.url)}</a></li>`);
  if (!links.length) return '';
  return `
    <div class="plugin-help-references">
      <span>${escapeHtml(t('pluginHelp.sources', 'Sources'))}</span>
      <ul>${links.join('')}</ul>
    </div>`;
}
