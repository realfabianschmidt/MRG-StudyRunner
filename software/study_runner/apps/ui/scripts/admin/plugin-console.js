import { getJson, postJson } from '../shared/api-client.js';
import { createModal } from '../shared/modal.js';
import { escapeHtml } from '../shared/dom-utils.js';
import { pluginByKey } from '../shared/plugin-catalog.js';
import { t } from '../shared/i18n.js';

let activeModal = null;
let eventSource = null;

export async function openPluginConsole(pluginKey, { showToast } = {}) {
  closeActiveConsole();
  const manifest = pluginByKey(pluginKey);
  const label = manifest?.ui?.label || pluginKey;
  const modal = createModal({
    title: t('pluginConsole.title', '{name} diagnostics').replace('{name}', label),
    variant: 'console',
    closeLabel: t('pluginConsole.close', 'Close diagnostics'),
    onClose: () => closeEventStream(),
  });
  activeModal = modal;
  modal.body.innerHTML = consoleMarkup(pluginKey);
  bindConsoleActions(modal, pluginKey, showToast);
  modal.open();

  try {
    await refreshConsole(modal, pluginKey);
    connectEventStream(modal, pluginKey);
  } catch (error) {
    renderError(modal, error);
    showToast?.(error.message || t('pluginConsole.openFailed', 'Could not open plugin diagnostics'), 'error');
  }
}

function closeActiveConsole() {
  closeEventStream();
  if (activeModal) {
    const previous = activeModal;
    activeModal = null;
    previous.destroy();
  }
}

function closeEventStream() {
  eventSource?.close();
  eventSource = null;
}

function consoleMarkup(pluginKey) {
  return `
    <section class="plugin-console-guided" aria-live="polite">
      <div class="plugin-console-heading">
        <div>
          <h3>${escapeHtml(t('pluginConsole.guided', 'Guided check'))}</h3>
          <p>${escapeHtml(t('pluginConsole.guidedHint', 'Connection, channels, signal quality and process state from the plugin itself.'))}</p>
        </div>
        <button type="button" class="btn-secondary btn-xs" data-console-refresh>
          <i class="iconoir-refresh"></i> ${escapeHtml(t('pluginConsole.refresh', 'Refresh'))}
        </button>
      </div>
      <div class="plugin-console-summary" data-console-summary>
        ${escapeHtml(t('pluginConsole.loading', 'Loading diagnostics ...'))}
      </div>
    </section>
    <details class="plugin-console-expert" open>
      <summary>${escapeHtml(t('pluginConsole.expert', 'Expert console'))}</summary>
      <p>${escapeHtml(t('pluginConsole.expertHint', 'Lines go directly to this plugin driver. This is not an operating-system shell.'))}</p>
      <pre class="plugin-console-output" data-console-output aria-live="polite"></pre>
      <div class="plugin-console-lock" data-console-lock hidden>
        <strong>${escapeHtml(t('pluginConsole.locked', 'Input is read-only while a study is running.'))}</strong>
        <label>
          <span>${escapeHtml(t('pluginConsole.reason', 'Reason for intervention'))}</span>
          <input type="text" maxlength="500" data-console-reason autocomplete="off">
        </label>
        <button type="button" class="btn-secondary" data-console-unlock>
          ${escapeHtml(t('pluginConsole.unlock', 'Unlock for 10 minutes'))}
        </button>
      </div>
      <form class="plugin-console-input" data-console-form>
        <label class="sr-only" for="plugin-console-line-${escapeHtml(pluginKey)}">${escapeHtml(t('pluginConsole.input', 'Plugin input line'))}</label>
        <input id="plugin-console-line-${escapeHtml(pluginKey)}" type="text" maxlength="16384" data-console-line autocomplete="off" spellcheck="false">
        <button type="submit" class="btn-primary" data-console-send>${escapeHtml(t('pluginConsole.send', 'Send'))}</button>
      </form>
      <div class="settings-hint plugin-console-state" data-console-state></div>
    </details>`;
}

function bindConsoleActions(modal, pluginKey, showToast) {
  modal.body.querySelector('[data-console-refresh]')?.addEventListener('click', () => {
    void refreshConsole(modal, pluginKey).catch((error) => renderError(modal, error));
  });
  modal.body.querySelector('[data-console-form]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = modal.body.querySelector('[data-console-line]');
    const line = input?.value ?? '';
    try {
      await postJson(`/api/admin/plugins/${encodeURIComponent(pluginKey)}/console/input`, { line });
      if (input) {
        input.value = '';
        input.focus();
      }
    } catch (error) {
      setState(modal, error.message, true);
      await refreshConsole(modal, pluginKey).catch(() => {});
    }
  });
  modal.body.querySelector('[data-console-unlock]')?.addEventListener('click', async () => {
    const reason = modal.body.querySelector('[data-console-reason]')?.value?.trim() || '';
    if (!reason) {
      setState(modal, t('pluginConsole.reasonRequired', 'Enter a reason before unlocking.'), true);
      return;
    }
    try {
      await postJson(`/api/admin/plugins/${encodeURIComponent(pluginKey)}/console/unlock`, {
        confirm: true,
        reason,
      });
      showToast?.(t('pluginConsole.unlocked', 'Plugin input unlocked for up to 10 minutes'), 'warning');
      await refreshConsole(modal, pluginKey);
    } catch (error) {
      setState(modal, error.message, true);
    }
  });
}

async function refreshConsole(modal, pluginKey) {
  const [snapshot, adminStatus] = await Promise.all([
    getJson(`/api/admin/plugins/${encodeURIComponent(pluginKey)}/console`),
    getJson('/api/admin/status'),
  ]);
  if (!modal.isOpen()) return;
  renderSnapshot(modal, snapshot, adminStatus?.plugins?.[pluginKey] || {});
}

function renderSnapshot(modal, snapshot, pluginStatus) {
  const output = modal.body.querySelector('[data-console-output]');
  if (output) {
    output.textContent = '';
    (snapshot.lines || []).forEach((entry) => appendOutput(output, entry));
    output.scrollTop = output.scrollHeight;
    output.dataset.lastSequence = String(snapshot.last_sequence || 0);
  }
  const summary = modal.body.querySelector('[data-console-summary]');
  if (summary) summary.innerHTML = renderGuidedSummary(snapshot, pluginStatus);
  const locked = Boolean(snapshot.study_running && !snapshot.console_unlocked);
  const lockBox = modal.body.querySelector('[data-console-lock]');
  if (lockBox) lockBox.hidden = !locked;
  const input = modal.body.querySelector('[data-console-line]');
  const send = modal.body.querySelector('[data-console-send]');
  if (input) input.disabled = locked || !snapshot.running;
  if (send) send.disabled = locked || !snapshot.running;
  setState(
    modal,
    snapshot.running
      ? (locked ? t('pluginConsole.readOnly', 'Process running; input locked for the active study.') : t('pluginConsole.ready', 'Process running; input ready.'))
      : t('pluginConsole.notRunning', 'Plugin process is not running.'),
    !snapshot.running,
  );
}

function renderGuidedSummary(snapshot, status) {
  const latest = status.latest && typeof status.latest === 'object' ? status.latest : {};
  const values = {
    process: snapshot.running ? `PID ${snapshot.pid}` : `exit ${snapshot.last_exit_code ?? '-'}`,
    status: first(status.status, status.state, '-'),
    device: first(status.connected_model, status.model, status.device_label, status.device, '-'),
    channels: first(status.supported_channels, status.channels, latest.supported_channels, latest.channels, '-'),
    resistance_ohm: first(status.resistances_ohm, status.resistance_ohm, latest.resistances_ohm, '-'),
    battery_percent: first(status.battery_percent, status.battery, latest.battery_percent, '-'),
    raw: first(status.raw_status, status.raw_eeg_status, status.eeg_status, '-'),
    derived: first(status.derived_status, status.emotions_status, status.metrics_status, '-'),
    measured_hz: first(status.measured_rate_hz, status.eeg_rate_hz, status.sample_rate_hz, latest.measured_rate_hz, '-'),
    batch_size: first(status.batch_size, status.last_batch_size, latest.batch_size, '-'),
    drops: first(status.drops, status.dropped_samples, status.queue_overflows, latest.drops, '-'),
    last_gap: first(status.last_gap, status.last_gap_samples, status.packet_gap, latest.last_gap, '-'),
  };
  return `<dl class="plugin-console-metrics">${Object.entries(values).map(([key, value]) => `
    <div><dt>${escapeHtml(key.replace(/_/g, ' '))}</dt><dd>${escapeHtml(formatValue(value))}</dd></div>
  `).join('')}</dl>
  <details class="plugin-console-status-json">
    <summary>${escapeHtml(t('pluginConsole.fullStatus', 'Complete plugin status'))}</summary>
    <pre>${escapeHtml(JSON.stringify(status, null, 2))}</pre>
  </details>`;
}

function first(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== '');
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(', ');
  if (value && typeof value === 'object') return JSON.stringify(value);
  return String(value ?? '-');
}

function connectEventStream(modal, pluginKey) {
  closeEventStream();
  const output = modal.body.querySelector('[data-console-output]');
  const after = Number(output?.dataset.lastSequence || 0);
  eventSource = new EventSource(
    `/api/admin/plugins/${encodeURIComponent(pluginKey)}/console/events?after=${encodeURIComponent(after)}`,
  );
  eventSource.onmessage = (event) => {
    if (!modal.isOpen() || !output) return;
    try {
      appendOutput(output, JSON.parse(event.data));
      output.scrollTop = output.scrollHeight;
    } catch {
      // Malformed output cannot execute in the UI and is simply ignored.
    }
  };
  eventSource.onerror = () => setState(
    modal,
    t('pluginConsole.streamRetry', 'Console stream interrupted; reconnecting ...'),
    true,
  );
}

function appendOutput(target, entry) {
  const timestamp = Number(entry.timestamp_epoch);
  const time = Number.isFinite(timestamp) ? new Date(timestamp * 1000).toLocaleTimeString() : '--:--:--';
  const source = String(entry.source || 'stdout');
  target.append(document.createTextNode(`${time} [${source}] ${String(entry.line ?? '')}\n`));
  target.dataset.lastSequence = String(entry.sequence || target.dataset.lastSequence || 0);
  while (target.childNodes.length > 1000) target.firstChild?.remove();
}

function setState(modal, message, isError = false) {
  const target = modal.body.querySelector('[data-console-state]');
  if (!target) return;
  target.textContent = message || '';
  target.classList.toggle('status-warning', Boolean(isError));
}

function renderError(modal, error) {
  const summary = modal.body.querySelector('[data-console-summary]');
  if (summary) summary.textContent = error.message || String(error);
  setState(modal, error.message || String(error), true);
}
