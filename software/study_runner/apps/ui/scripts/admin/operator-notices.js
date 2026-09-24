/**
 * Operator notices: every error the tablet showed and every participant
 * action the server refused (runtime_core/studies/operator_notices.py).
 *
 * A new notice appears once as a toast and stays in the stack until it is
 * clicked, also across page reloads, because acknowledging is stored on the
 * server.
 */
import { getJson, postJson } from '../shared/api-client.js';
import { t } from '../shared/i18n.js';
import { escapeHtml, formatDateTime } from '../shared/dom-utils.js';

const POLL_INTERVAL_MS = 3000;
const REQUEST_TIMEOUT_MS = 2500;
const ICONS = { error: 'iconoir-xmark-circle', warning: 'iconoir-warning-triangle', info: 'iconoir-info-circle' };

export function initializeOperatorNotices({ showToast } = {}) {
  const stack = document.createElement('div');
  stack.className = 'operator-notices';
  stack.setAttribute('aria-live', 'polite');
  document.body.appendChild(stack);

  const toasted = new Set();
  let inFlight = false;

  const render = (notices) => {
    stack.replaceChildren(...notices.map((notice) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `operator-notice operator-notice--${notice.severity}`;
      item.title = t('notices.dismiss', 'Click to dismiss');
      const source = notice.source === 'tablet'
        ? t('notices.sourceTablet', 'Tablet')
        : t('notices.sourceServer', 'Study Runner');
      const when = notice.created_at_epoch ? formatDateTime(new Date(notice.created_at_epoch * 1000).toISOString()) : '';
      item.innerHTML = `
        <i class="${ICONS[notice.severity] || ICONS.info}"></i>
        <span class="operator-notice-body">
          <span class="operator-notice-meta">${escapeHtml(source)}${when ? ` · ${escapeHtml(when)}` : ''}</span>
          <span class="operator-notice-message">${escapeHtml(notice.message)}</span>
        </span>`;
      item.addEventListener('click', async () => {
        item.remove();
        try {
          await postJson('/api/admin/notices/ack', { id: notice.id }, { timeoutMs: REQUEST_TIMEOUT_MS });
        } catch (error) {
          console.warn('[admin] Could not dismiss notice:', error);
        }
      });
      return item;
    }));
    stack.hidden = notices.length === 0;
  };

  const poll = async () => {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const response = await getJson('/api/admin/notices', { timeoutMs: REQUEST_TIMEOUT_MS });
      const notices = Array.isArray(response?.notices) ? response.notices : [];
      for (const notice of notices) {
        if (toasted.has(notice.id)) continue;
        toasted.add(notice.id);
        showToast?.(notice.message, notice.severity === 'info' ? 'info' : notice.severity);
      }
      render(notices);
    } catch (error) {
      console.warn('[admin] Could not load notices:', error);
    } finally {
      inFlight = false;
    }
  };

  void poll();
  setInterval(() => void poll(), POLL_INTERVAL_MS);
}
