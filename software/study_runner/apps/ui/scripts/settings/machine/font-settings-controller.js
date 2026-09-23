/**
 * The fonts settings page, next to Logos.
 *
 * Headings and body text each use a built-in font or one uploaded font file.
 * The choice applies to the admin pages and the participant page of this
 * computer; uploaded files stay in the local settings folder.
 */
import { t } from '../../shared/i18n.js';
import { escapeHtml } from '../../shared/dom-utils.js';
import { applyFonts, loadFonts } from '../../shared/branding.js';

const PANEL_ROOT_ID = 'font-settings-panel';
const SLOTS = ['heading', 'body'];
const ACCEPT = '.woff2,.woff,.ttf,.otf';

let callbacks = {};
let fonts = null;

export function initializeFontSettings(options = {}) {
  callbacks = options;
}

export function renderFontSettingsPanel() {
  return `
    <article class="dashboard-card dashboard-card--wide">
      <div class="dashboard-card-title">
        <i class="iconoir-text-size"></i> <span>${escapeHtml(t('fonts.title', 'Fonts'))}</span>
      </div>
      <p class="settings-hint">${escapeHtml(t('fonts.subtitle', 'Fonts for headings and body text on the admin pages and the participant page of this computer.'))}</p>
      <div id="${PANEL_ROOT_ID}"></div>
      <p class="settings-hint">${escapeHtml(t('fonts.rightsHint', 'Only upload fonts you are allowed to use on this computer. Uploaded fonts stay in the local settings folder and are never part of a release or a study package.'))}</p>
    </article>`;
}

export async function refreshFontSettings() {
  fonts = await loadFonts();
  render();
}

function choiceLabel(choice) {
  return {
    default: t('fonts.choice.default', 'Default'),
    geist: t('fonts.choice.geist', 'Geist'),
    system: t('fonts.choice.system', 'System font'),
    serif: t('fonts.choice.serif', 'Serif'),
    uploaded: t('fonts.choice.uploaded', 'Uploaded font'),
  }[choice];
}

function render() {
  const root = document.getElementById(PANEL_ROOT_ID);
  if (!root) return;
  if (!fonts) {
    root.innerHTML = `<p class="settings-hint">${escapeHtml(t('fonts.loadFailed', 'The font settings could not be loaded.'))}</p>`;
    return;
  }
  root.innerHTML = SLOTS.map((slot) => {
    const entry = fonts[slot] || {};
    const choices = ['default', 'geist', 'system', 'serif', ...(entry.has_upload ? ['uploaded'] : [])];
    return `
      <div class="branding-slot">
        <div class="branding-slot-head">
          <strong>${escapeHtml(slot === 'heading' ? t('fonts.headingLabel', 'Headings') : t('fonts.bodyLabel', 'Body text'))}</strong>
          ${entry.has_upload ? `<span class="settings-hint">${escapeHtml(t('fonts.uploadedName', 'Uploaded: {name}').replace('{name}', entry.name || ''))}</span>` : ''}
        </div>
        <div class="branding-asset-actions">
          <select class="fi-input" data-font-choice="${slot}" aria-label="${escapeHtml(t('fonts.choiceLabel', 'Font'))}">
            ${choices.map((choice) => `<option value="${choice}"${choice === entry.choice ? ' selected' : ''}>${escapeHtml(choiceLabel(choice))}</option>`).join('')}
          </select>
          <input type="file" accept="${ACCEPT}" data-font-upload="${slot}" hidden>
          <button class="btn-secondary" type="button" data-font-pick="${slot}">
            <i class="iconoir-upload"></i> <span>${escapeHtml(entry.has_upload ? t('fonts.replace', 'Replace font') : t('fonts.upload', 'Upload font'))}</span>
          </button>
          ${entry.has_upload ? `<button class="btn-secondary" type="button" data-font-remove="${slot}"><i class="iconoir-trash"></i> <span>${escapeHtml(t('fonts.remove', 'Remove'))}</span></button>` : ''}
        </div>
      </div>`;
  }).join('') + `<p class="settings-hint">${escapeHtml(t('fonts.formatHint', 'WOFF2, WOFF, TTF or OTF, up to 5 MB each.'))}</p>`;

  root.querySelectorAll('[data-font-choice]').forEach((select) => {
    select.addEventListener('change', () => void choose(select.dataset.fontChoice, select.value));
  });
  root.querySelectorAll('[data-font-pick]').forEach((button) => {
    button.addEventListener('click', () => root.querySelector(`[data-font-upload="${button.dataset.fontPick}"]`)?.click());
  });
  root.querySelectorAll('[data-font-upload]').forEach((input) => {
    input.addEventListener('change', (event) => void upload(input.dataset.fontUpload, event));
  });
  root.querySelectorAll('[data-font-remove]').forEach((button) => {
    button.addEventListener('click', () => void remove(button.dataset.fontRemove));
  });
}

async function send(url, options, failureMessage) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.ok) throw new Error(payload.error || failureMessage);
  fonts = payload.fonts;
  render();
  await applyFonts(fonts);
}

async function choose(slot, choice) {
  try {
    await send('/api/admin/branding/fonts', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [slot]: choice }),
    }, t('fonts.saveFailed', 'The font could not be changed.'));
    callbacks.showToast?.(t('fonts.saved', 'Font saved'), 'success');
  } catch (error) {
    callbacks.showToast?.(String(error.message || error), 'error');
    void refreshFontSettings();
  }
}

async function upload(slot, event) {
  const file = event.target?.files?.[0];
  if (!file) return;
  event.target.value = '';
  const body = new FormData();
  body.append('file', file);
  try {
    await send(`/api/admin/branding/font/${slot}`, { method: 'POST', body }, t('fonts.uploadFailed', 'The font could not be saved.'));
    callbacks.showToast?.(t('fonts.saved', 'Font saved'), 'success');
  } catch (error) {
    callbacks.showToast?.(String(error.message || error), 'error');
  }
}

async function remove(slot) {
  try {
    await send(`/api/admin/branding/font/${slot}`, { method: 'DELETE' }, t('fonts.removeFailed', 'The font could not be removed.'));
    callbacks.showToast?.(t('fonts.removed', 'Font removed'), 'success');
  } catch (error) {
    callbacks.showToast?.(String(error.message || error), 'error');
  }
}
