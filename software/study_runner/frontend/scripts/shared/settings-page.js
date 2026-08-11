/**
 * The shared skeleton behind core and manifest-driven settings pages.
 *
 * All three pages are the same thing: a view you open from the hub, a status
 * block you refresh, numbered setup steps that show what is still missing, and
 * buttons that run one action and report the outcome. This module owns that
 * behavior so the pages only describe their own content.
 */
import { byId, escapeHtml } from './dom-utils.js';

/**
 * Mark one setup step as ready / missing / optional.
 * The state drives the colour via the `data-state` attribute in main.css.
 */
export function setStepState(id, state, label) {
  const target = byId(id);
  if (!target) return;
  target.textContent = label;
  target.dataset.state = state;
}

/**
 * Render the result of a "Test connection" action.
 * Accepts either a list of named checks or a single ok/error result, so every
 * page can show the same box regardless of how detailed its backend answer is.
 */
export function renderTestResult(containerId, result, { fallbackErrorLabel = 'Failed' } = {}) {
  const container = byId(containerId);
  if (!container) return;

  const checks = Array.isArray(result?.checks) && result.checks.length
    ? result.checks
    : [{ ok: Boolean(result?.ok), name: result?.ok ? 'OK' : fallbackErrorLabel, message: result?.error || result?.message || '' }];

  const rows = checks.map((check) => {
    const iconClass = check.ok === true
      ? 'iconoir-check-circle'
      : check.ok === false
        ? 'iconoir-xmark-circle'
        : 'iconoir-info-circle';
    const message = check.message ? ` - ${escapeHtml(check.message)}` : '';
    return `<div class="test-row"><i class="${iconClass}"></i><span><strong>${escapeHtml(check.name)}</strong>${message}</span></div>`;
  }).join('');

  container.innerHTML = `<div class="test-result-box ${result?.ok ? 'test-result-box--ok' : 'test-result-box--fail'}">${rows}</div>`;
  container.hidden = false;
}

/**
 * Run an action with the usual button feedback: disabled, spinner label, restore.
 * Keeps every page's buttons behaving the same way, including on failure.
 */
export async function withBusyButton(buttonId, busyLabel, action) {
  const button = byId(buttonId);
  const originalHtml = button?.innerHTML ?? '';
  if (button) {
    button.disabled = true;
    button.innerHTML = `<i class="iconoir-refresh"></i> ${escapeHtml(busyLabel)}`;
  }
  try {
    return await action();
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = originalHtml;
    }
  }
}
