/**
 * The one modal implementation.
 *
 * Before this, every dialog re-created the same `.modal-backdrop` /
 * `.settings-modal` markup plus its own Escape and backdrop-click handlers.
 * This wraps the existing CSS so new dialogs look and behave identically.
 */
import { escapeHtml } from './dom-utils.js';

export function createModal({ kicker = '', title = '', variant = '', closeLabel = 'Close', onClose = null } = {}) {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.hidden = true;

  const variantClass = variant ? ` settings-modal--${variant}` : '';
  backdrop.innerHTML = `
    <div class="settings-modal${variantClass}" role="dialog" aria-modal="true">
      <div class="settings-modal-header">
        <div>
          ${kicker ? `<span class="dashboard-kicker">${escapeHtml(kicker)}</span>` : ''}
          <h2>${escapeHtml(title)}</h2>
        </div>
        <button class="overlay-close" type="button" aria-label="${escapeHtml(closeLabel)}" title="${escapeHtml(closeLabel)}">
          <i class="iconoir-xmark"></i>
        </button>
      </div>
      <div class="settings-modal-body"></div>
    </div>
  `;

  const body = backdrop.querySelector('.settings-modal-body');
  const dialog = backdrop.querySelector('.settings-modal');
  let escapeHandler = null;

  function isOpen() {
    return !backdrop.hidden;
  }

  function close() {
    if (!isOpen()) return;
    backdrop.hidden = true;
    if (escapeHandler) {
      document.removeEventListener('keydown', escapeHandler);
      escapeHandler = null;
    }
    onClose?.();
  }

  function open() {
    if (!backdrop.isConnected) document.body.appendChild(backdrop);
    backdrop.hidden = false;
    escapeHandler = (event) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', escapeHandler);
    dialog?.querySelector('button, [href], input, select, textarea')?.focus?.();
  }

  backdrop.querySelector('.overlay-close')?.addEventListener('click', close);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });

  function setTitle(value) {
    const heading = dialog?.querySelector('h2');
    if (heading) heading.textContent = value;
  }

  function setKicker(value) {
    const kickerEl = dialog?.querySelector('.dashboard-kicker');
    if (kickerEl) kickerEl.textContent = value;
  }

  function destroy() {
    close();
    backdrop.remove();
  }

  return { element: backdrop, body, open, close, isOpen, setTitle, setKicker, destroy };
}
