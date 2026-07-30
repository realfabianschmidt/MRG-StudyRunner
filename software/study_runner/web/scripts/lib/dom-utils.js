/**
 * Small DOM helpers every page needs.
 *
 * escapeHtml used to be copy-pasted into every controller and card module.
 * Import it from here instead: one implementation, one place to fix.
 */

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function byId(id) {
  return document.getElementById(id);
}

export function setText(id, value) {
  const target = byId(id);
  if (target) target.textContent = value;
}

export function setHtml(id, markup) {
  const target = byId(id);
  if (target) target.innerHTML = markup;
}

export function setHidden(id, hidden) {
  const target = byId(id);
  if (target) target.hidden = Boolean(hidden);
}

/** Format a byte count for operators: "1.4 MB", not "1468006". */
export function formatFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '-';
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB'];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size < 10 ? size.toFixed(1) : Math.round(size)} ${units[unitIndex]}`;
}

/** Format an ISO timestamp in the viewer's locale, or "-" when unusable. */
export function formatDateTime(isoValue) {
  if (!isoValue) return '-';
  const parsed = new Date(isoValue);
  if (Number.isNaN(parsed.getTime())) return String(isoValue);
  return parsed.toLocaleString();
}
