/**
 * The branding settings page.
 *
 * The waiting slide is the first thing a participant sees, so it carries the
 * research group's mark and, optionally, the logos of whoever funded the work.
 * Which marks those are is an operator decision, not a code change - this page
 * uploads and removes them.
 *
 * The panel markup is generated here rather than sitting in admin.html because
 * the funder list has no fixed length; the shell just asks for the HTML.
 */
import { t } from '../../i18n.js';
import { escapeHtml } from '../../lib/dom-utils.js';
import { brandingAssetUrl, loadBranding } from '../../lib/branding.js';

const PANEL_ROOT_ID = 'branding-settings-panel';

let callbacks = {};
let branding = { group: null, funders: [] };

export function initializeBrandingSettings(options = {}) {
  callbacks = options;
}

/** Markup for the shell. Data arrives later through refreshBrandingSettings(). */
export function renderBrandingSettingsPanel() {
  return `
    <article class="dashboard-card dashboard-card--wide">
      <div class="dashboard-card-title">
        <i class="iconoir-media-image"></i> <span>${escapeHtml(t('branding.title', 'Logos'))}</span>
      </div>
      <p class="settings-hint">${escapeHtml(t('branding.subtitle', 'These logos appear on the waiting screen the participant sees before a study starts. The group logo also appears in the hub.'))}</p>
      <div id="${PANEL_ROOT_ID}"></div>
    </article>`;
}

/** Called by the shell when the panel is shown. */
export async function refreshBrandingSettings() {
  branding = await loadBranding();
  render();
}

function render() {
  const root = document.getElementById(PANEL_ROOT_ID);
  if (!root) return;

  root.innerHTML = `
    <div class="branding-slot">
      <div class="branding-slot-head">
        <strong>${escapeHtml(t('branding.groupLabel', 'Research group logo'))}</strong>
        <span class="settings-hint">${escapeHtml(t('branding.groupHint', 'Shown above the study name on the waiting screen.'))}</span>
      </div>
      ${renderSlot('group', branding.group)}
    </div>
    <div class="branding-slot">
      <div class="branding-slot-head">
        <strong>${escapeHtml(t('branding.fundersLabel', 'Funder logos'))}</strong>
        <span class="settings-hint">${escapeHtml(t('branding.fundersHint', 'Shown small in the bottom right of the waiting screen only.'))}</span>
      </div>
      ${branding.funders.map((funder) => renderSlot(funder.slot, funder)).join('')}
      ${renderAddFunder()}
    </div>
    <p class="settings-hint">${escapeHtml(t('branding.formatHint', 'SVG, PNG, JPG or WebP, up to 1 MB each.'))}</p>`;

  root.querySelectorAll('[data-branding-upload]').forEach((input) => {
    input.addEventListener('change', (event) => void upload(input.dataset.brandingUpload, event));
  });
  root.querySelectorAll('[data-branding-remove]').forEach((button) => {
    button.addEventListener('click', () => void remove(button.dataset.brandingRemove));
  });
  root.querySelectorAll('[data-branding-pick]').forEach((button) => {
    button.addEventListener('click', () => {
      root.querySelector(`[data-branding-upload="${button.dataset.brandingPick}"]`)?.click();
    });
  });
}

function renderSlot(slot, asset) {
  const inputId = `branding-file-${slot.replace(':', '-')}`;
  return `
    <div class="branding-asset">
      <div class="branding-preview">
        ${asset ? `<img src="${escapeHtml(brandingAssetUrl(slot, String(Date.now())))}" alt="${escapeHtml(asset.alt || '')}">` : `<span class="settings-hint">${escapeHtml(t('branding.empty', 'No logo yet'))}</span>`}
      </div>
      <div class="branding-asset-actions">
        <input type="file" id="${inputId}" accept=".svg,.png,.jpg,.jpeg,.webp" data-branding-upload="${escapeHtml(slot)}" hidden>
        <button class="btn-secondary" type="button" data-branding-pick="${escapeHtml(slot)}">
          <i class="iconoir-upload"></i> <span>${escapeHtml(asset ? t('branding.replace', 'Replace') : t('branding.upload', 'Upload'))}</span>
        </button>
        ${asset ? `<button class="btn-secondary" type="button" data-branding-remove="${escapeHtml(slot)}"><i class="iconoir-trash"></i> <span>${escapeHtml(t('branding.remove', 'Remove'))}</span></button>` : ''}
      </div>
    </div>`;
}

function renderAddFunder() {
  return `
    <div class="branding-asset branding-asset--add">
      <input type="file" id="branding-file-funder-new" accept=".svg,.png,.jpg,.jpeg,.webp" data-branding-upload="funder" hidden>
      <button class="btn-secondary" type="button" data-branding-pick="funder">
        <i class="iconoir-plus"></i> <span>${escapeHtml(t('branding.addFunder', 'Add funder logo'))}</span>
      </button>
    </div>`;
}

async function upload(slot, event) {
  const file = event.target?.files?.[0];
  if (!file) return;
  // Let the same file be picked twice in a row after a failure.
  event.target.value = '';

  const body = new FormData();
  body.append('file', file);
  try {
    const response = await fetch(`/api/admin/branding/${encodeURIComponent(slot)}`, { method: 'POST', body });
    const payload = await readJson(response, t('branding.uploadFailed', 'The logo could not be saved.'));
    if (!response.ok || !payload.ok) throw new Error(payload.error || t('branding.uploadFailed', 'The logo could not be saved.'));
    branding = payload.branding;
    render();
    callbacks.showToast?.(t('branding.saved', 'Logo saved'), 'success');
    callbacks.onBrandingChanged?.(branding);
  } catch (error) {
    callbacks.showToast?.(String(error.message || error), 'error');
  }
}

async function remove(slot) {
  const confirmed = await callbacks.confirmWithModal?.({
    title: t('branding.removeTitle', 'Remove logo'),
    message: t('branding.removeBody', 'Remove this logo from the waiting screen?'),
    confirmLabel: t('branding.remove', 'Remove'),
  });
  if (confirmed === false) return;

  try {
    const response = await fetch(`/api/admin/branding/${encodeURIComponent(slot)}`, { method: 'DELETE' });
    const payload = await readJson(response, t('branding.removeFailed', 'The logo could not be removed.'));
    if (!response.ok || !payload.ok) throw new Error(payload.error || t('branding.removeFailed', 'The logo could not be removed.'));
    branding = payload.branding;
    render();
    callbacks.showToast?.(t('branding.removed', 'Logo removed'), 'success');
    callbacks.onBrandingChanged?.(branding);
  } catch (error) {
    callbacks.showToast?.(String(error.message || error), 'error');
  }
}

/**
 * Parse a response that is supposed to be JSON.
 *
 * When it is not, the body is an error page - a 404 because the browser is
 * still running a build without this endpoint, a proxy notice, a traceback.
 * Reporting "Unexpected token <" for any of those tells the operator nothing,
 * so say what actually came back instead.
 */
async function readJson(response, fallbackMessage) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    const detail = response.status === 404
      ? t('branding.staleClient', 'The page is running an older version of Study Runner. Reload this page and try again.')
      : `${response.status} ${response.statusText}`.trim();
    throw new Error(`${fallbackMessage} (${detail})`);
  }
}
