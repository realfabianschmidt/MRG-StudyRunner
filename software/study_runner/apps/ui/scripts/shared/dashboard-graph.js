/**
 * The one graph layout every sensor tile uses: title, graph, then the legend
 * (the indicators) on the left and every explanation hidden in a small (i)
 * on the right. Plugins get these through the dashboard `ui` helpers.
 */
import { escapeHtml } from './dom-utils.js';

/** A small (i): the text shows on hover and focus and is read by screen readers. */
export function infoTip(text) {
  const safe = escapeHtml(text);
  return `<span class="info-tip" tabindex="0" role="img" title="${safe}" aria-label="${safe}"><i class="iconoir-info-circle" aria-hidden="true"></i></span>`;
}

/**
 * `svg` and `legend` are HTML the plugin built (already escaped);
 * `title` and `info` are plain text.
 */
export function graphSection({ title = '', svg = '', legend = '', info = '' } = {}) {
  return `<section class="sensor-graph" aria-label="${escapeHtml(title)}">
    <div class="sensor-graph-title">${escapeHtml(title)}</div>
    ${svg}
    <div class="sensor-graph-foot"><span class="sensor-graph-legend">${legend}</span>${info ? infoTip(info) : ''}</div>
  </section>`;
}
