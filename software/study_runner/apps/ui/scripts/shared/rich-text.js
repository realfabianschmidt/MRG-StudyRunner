/**
 * Safe formatted text and the text/image layout shared by the info card and
 * the study cover page.
 *
 * Everything is HTML-escaped first; only a tiny Markdown subset is then turned
 * into markup: "# " / "## " headings, "- " list items, **bold**, *italic*,
 * paragraphs separated by a blank line, and single line breaks. There are no
 * links and no raw HTML, so study text can never inject markup or script.
 */
import { escapeHtml } from './dom-utils.js';

export const MEDIA_LAYOUTS = ['text', 'image-left', 'image-right'];

function inline(escaped) {
  return escaped
    .replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
}

export function renderRichText(text) {
  const blocks = String(text ?? '').replace(/\r\n?/g, '\n').split(/\n{2,}/);
  return blocks.map((block) => {
    const lines = block.split('\n').filter((line, index, all) => line.trim() || (index > 0 && index < all.length - 1));
    if (!lines.length) return '';
    if (lines.every((line) => /^\s*-\s+/.test(line))) {
      return `<ul>${lines.map((line) => `<li>${inline(escapeHtml(line.replace(/^\s*-\s+/, '')))}</li>`).join('')}</ul>`;
    }
    const heading = lines.length === 1 && /^(#{1,2})\s+(.+)$/.exec(lines[0].trim());
    if (heading) {
      const level = heading[1].length === 1 ? 'h2' : 'h3';
      return `<${level}>${inline(escapeHtml(heading[2]))}</${level}>`;
    }
    return `<p>${lines.map((line) => inline(escapeHtml(line))).join('<br>')}</p>`;
  }).join('');
}

export function studyAssetUrl(assetId) {
  return assetId ? `/api/study-assets/${encodeURIComponent(assetId)}` : '';
}

/**
 * Title, formatted text and an optional image beside it. Narrow screens stack
 * the image above the text (see .media-layout in main.css).
 */
export function renderMediaLayout({ title = '', text = '', image_asset: imageAsset = '', image_alt: imageAlt = '', layout = 'text' } = {}) {
  const hasImage = Boolean(imageAsset) && layout !== 'text';
  const mode = hasImage && MEDIA_LAYOUTS.includes(layout) ? layout : 'text';
  const textColumn = `<div class="media-layout-text">
      ${title ? `<h1 class="screen-title media-layout-title">${escapeHtml(title)}</h1>` : ''}
      <div class="media-layout-body">${renderRichText(text)}</div>
    </div>`;
  if (mode === 'text') return `<div class="media-layout media-layout--text">${textColumn}</div>`;
  const image = `<figure class="media-layout-figure"><img src="${escapeHtml(studyAssetUrl(imageAsset))}" alt="${escapeHtml(imageAlt)}" loading="eager"></figure>`;
  return `<div class="media-layout media-layout--${mode}">${mode === 'image-left' ? image + textColumn : textColumn + image}</div>`;
}
