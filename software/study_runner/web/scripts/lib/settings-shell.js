/**
 * The shared settings shell: a left-hand nav, a right-hand panel.
 *
 * Both settings surfaces are the same thing with different content - the
 * machine-level hub opened from the admin hub, and the per-study panel opened
 * from the study editor. This module owns the one behaviour they share:
 * exactly one nav entry is active, exactly one panel is visible.
 *
 * Deliberately flat. Selecting an entry reveals its panel and nothing else -
 * a panel never leads to another screen. If content needs its own screen it
 * becomes another nav entry instead, which is what keeps the right-hand side
 * from growing sub-navigation.
 */
import { escapeHtml } from './dom-utils.js';

/**
 * Render the nav buttons for a shell.
 *
 * `entries` are `{key, icon, label, group}`. Entries sharing a `group` are
 * kept together under that heading, in first-seen order.
 */
export function renderShellNav(entries, activeKey) {
  const groups = [];
  entries.forEach((entry) => {
    const name = entry.group || '';
    let group = groups.find((candidate) => candidate.name === name);
    if (!group) {
      group = { name, items: [] };
      groups.push(group);
    }
    group.items.push(entry);
  });

  return groups.map((group) => `
    <div class="sidebar-section">
      ${group.name ? `<div class="sidebar-section-title"><span>${escapeHtml(group.name)}</span></div>` : ''}
      ${group.items.map((entry) => `
        <button class="settings-nav-item${entry.key === activeKey ? ' active' : ''}" type="button"
                data-shell-nav="${escapeHtml(entry.key)}"
                aria-current="${entry.key === activeKey ? 'page' : 'false'}">
          <i class="${escapeHtml(entry.icon || 'iconoir-settings')}"></i><span>${escapeHtml(entry.label)}</span>
        </button>`).join('')}
    </div>`).join('');
}

/** Wrap one panel's content so the shell can show/hide it by key. */
export function renderShellPanel(key, content, hidden) {
  const body = Array.isArray(content) ? content.join('') : String(content ?? '');
  return `
    <div class="settings-shell-panel" data-shell-panel="${escapeHtml(key)}"${hidden ? ' hidden' : ''}>
      ${body}
    </div>`;
}

/**
 * Show one panel and mark its nav entry active.
 * Returns the key that ended up active, which may differ from the requested
 * one when that panel does not exist (e.g. a plugin disappeared on reload).
 */
export function activateShellPanel(root, key) {
  if (!root) return '';
  const panels = [...root.querySelectorAll('[data-shell-panel]')];
  const exists = panels.some((panel) => panel.dataset.shellPanel === key);
  const activeKey = exists ? key : (panels[0]?.dataset.shellPanel || '');

  panels.forEach((panel) => {
    panel.hidden = panel.dataset.shellPanel !== activeKey;
  });
  root.querySelectorAll('[data-shell-nav]').forEach((item) => {
    const active = item.dataset.shellNav === activeKey;
    item.classList.toggle('active', active);
    item.setAttribute('aria-current', active ? 'page' : 'false');
  });
  return activeKey;
}

/**
 * Bind nav clicks once per render. `onSelect` receives the newly active key,
 * so the caller can remember it across re-renders.
 */
export function bindShellNav(root, onSelect) {
  if (!root) return;
  root.querySelectorAll('[data-shell-nav]').forEach((item) => {
    item.addEventListener('click', () => {
      const key = activateShellPanel(root, item.dataset.shellNav || '');
      onSelect?.(key);
    });
  });
}
