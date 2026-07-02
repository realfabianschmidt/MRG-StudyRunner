// Shared "info text" for every card.
//
// Two optional strings live on any question object:
//   info_top    - a short highlighted callout shown above the card body
//   info_bottom - a plain note (no background) shown below the card body
//
// These render the same way for every card type, so the mechanism lives here
// once instead of being copied into each card module.

import { t } from '../i18n.js';

function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function renderInfoTop(q) {
  const text = String(q?.info_top ?? '').trim();
  if (!text) return '';
  return `<div class="card-info-top">${escapeHtml(text)}</div>`;
}

export function renderInfoBottom(q) {
  const text = String(q?.info_bottom ?? '').trim();
  if (!text) return '';
  return `<div class="card-info-bottom">${escapeHtml(text)}</div>`;
}

export function renderInfoEditor(q) {
  return `
    <div class="field">
      <label>${escapeHtml(t('cards.info.topLabel', 'Highlighted info (top)'))}</label>
      <textarea class="fi-textarea ci-info-top" rows="2" placeholder="${escapeHtml(t('cards.info.topPlaceholder', 'Optional highlighted note shown above this card'))}">${escapeHtml(q?.info_top ?? '')}</textarea>
    </div>
    <div class="field">
      <label>${escapeHtml(t('cards.info.bottomLabel', 'Note (bottom)'))}</label>
      <textarea class="fi-textarea ci-info-bottom" rows="2" placeholder="${escapeHtml(t('cards.info.bottomPlaceholder', 'Optional plain note shown below this card'))}">${escapeHtml(q?.info_bottom ?? '')}</textarea>
    </div>`;
}

export function collectInfo(el) {
  const result = {};
  const top = el?.querySelector('.ci-info-top')?.value.trim() || '';
  const bottom = el?.querySelector('.ci-info-bottom')?.value.trim() || '';
  if (top) result.info_top = top;
  if (bottom) result.info_bottom = bottom;
  return result;
}
