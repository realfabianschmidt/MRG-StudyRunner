/**
 * Editor fields for read-only participant content (info card, cover page):
 * title, formatted text, an optional uploaded image, its description, and the
 * layout. Changes are announced with a bubbling `input` event so the
 * surrounding editor refreshes its preview and marks the study unsaved.
 */
import { t } from './i18n.js';
import { escapeHtml } from './dom-utils.js';
import { MEDIA_LAYOUTS, studyAssetUrl } from './rich-text.js';

// A mixed extension/MIME list greys out valid JPEG/PNG files in the macOS
// picker; the server checks the real content (PNG, JPEG, WebP, SVG) anyway.
const ACCEPT = 'image/*';

export function renderMediaEditor(content = {}, { titleLabel, textLabel } = {}) {
  const asset = content.image_asset || '';
  const layout = MEDIA_LAYOUTS.includes(content.layout) ? content.layout : 'text';
  const layoutLabels = {
    text: t('media.layout.text', 'Text only'),
    'image-left': t('media.layout.imageLeft', 'Image left, text right'),
    'image-right': t('media.layout.imageRight', 'Text left, image right'),
  };
  return `
    <div class="field">
      <label>${escapeHtml(titleLabel || t('media.titleLabel', 'Headline'))}</label>
      <input type="text" class="fi-input me-title" maxlength="200" value="${escapeHtml(content.title || '')}">
    </div>
    <div class="field">
      <label>${escapeHtml(textLabel || t('media.textLabel', 'Text'))}</label>
      <textarea class="fi-textarea me-text" rows="8">${escapeHtml(content.text || '')}</textarea>
      <p class="editor-hint me-hint">${escapeHtml(t('media.formatHint', 'Formatting: a blank line starts a new paragraph, "# " a heading, "- " a list item, **bold**, *italic*.'))}</p>
    </div>
    <div class="field me-image-field">
      <label>${escapeHtml(t('media.imageLabel', 'Image (optional)'))}</label>
      <input type="hidden" class="me-asset" value="${escapeHtml(asset)}">
      <div class="me-image-row">
        <img class="me-preview" alt="" src="${escapeHtml(studyAssetUrl(asset))}"${asset ? '' : ' hidden'}>
        <label class="btn-secondary me-upload">
          <i class="iconoir-upload"></i> ${escapeHtml(t('media.chooseImage', 'Choose image'))}
          <input type="file" class="me-file" accept="${ACCEPT}" hidden>
        </label>
        <button type="button" class="btn-secondary me-remove"${asset ? '' : ' hidden'}>${escapeHtml(t('media.removeImage', 'Remove image'))}</button>
      </div>
      <p class="editor-hint">${escapeHtml(t('media.imageFormats', 'PNG, JPG, WebP or SVG, at most 5 MB.'))}</p>
      <p class="editor-hint me-status" role="status"></p>
    </div>
    <div class="field me-requires-image"${asset ? '' : ' hidden'}>
      <label>${escapeHtml(t('media.altLabel', 'Image description (for screen readers)'))}</label>
      <input type="text" class="fi-input me-alt" maxlength="300" value="${escapeHtml(content.image_alt || '')}">
    </div>
    <div class="field me-requires-image"${asset ? '' : ' hidden'}>
      <label>${escapeHtml(t('media.layoutLabel', 'Layout'))}</label>
      <select class="fi-input me-layout">
        ${MEDIA_LAYOUTS.map((value) => `<option value="${value}"${value === layout ? ' selected' : ''}>${escapeHtml(layoutLabels[value])}</option>`).join('')}
      </select>
    </div>`;
}

export function collectMediaEditor(root) {
  const asset = root.querySelector('.me-asset')?.value || '';
  const layout = root.querySelector('.me-layout')?.value || 'text';
  return {
    title: root.querySelector('.me-title')?.value.trim() || '',
    text: root.querySelector('.me-text')?.value || '',
    image_asset: asset,
    image_alt: asset ? root.querySelector('.me-alt')?.value.trim() || '' : '',
    // A freshly added image should be visible without a second click.
    layout: asset ? (layout === 'text' ? 'image-left' : layout) : 'text',
  };
}

function setAsset(root, assetId) {
  root.querySelector('.me-asset').value = assetId;
  const preview = root.querySelector('.me-preview');
  preview.src = studyAssetUrl(assetId);
  preview.hidden = !assetId;
  root.querySelector('.me-remove').hidden = !assetId;
  root.querySelectorAll('.me-requires-image').forEach((node) => { node.hidden = !assetId; });
  const layout = root.querySelector('.me-layout');
  if (assetId && layout.value === 'text') layout.value = 'image-left';
  if (!assetId) layout.value = 'text';
  root.querySelector('.me-asset').dispatchEvent(new Event('input', { bubbles: true }));
}

export function bindMediaEditor(root) {
  const status = root.querySelector('.me-status');
  root.querySelector('.me-file')?.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    status.textContent = t('media.uploading', 'Uploading…');
    const body = new FormData();
    body.append('file', file);
    try {
      const response = await fetch('/api/admin/study-assets', { method: 'POST', body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.asset_id) throw new Error(payload.error || `HTTP ${response.status}`);
      setAsset(root, payload.asset_id);
      status.textContent = '';
    } catch (error) {
      status.textContent = `${t('media.uploadFailed', 'The image could not be uploaded')}: ${error.message}`;
    }
  });
  root.querySelector('.me-remove')?.addEventListener('click', () => setAsset(root, ''));
}
