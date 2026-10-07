import { t } from '../../shared/i18n.js';
import { escapeHtml } from '../../shared/dom-utils.js';
import { getJson, postJson } from '../../shared/api-client.js';

const ENDPOINT = '/api/admin/recording-timing';

export function renderRecordingTimingPanel() {
  return `
    <article class="dashboard-card dashboard-card--wide" id="recording-timing-panel">
      <div class="dashboard-card-title"><i class="iconoir-timer"></i> <span>${escapeHtml(t('recordingTiming.title', 'Recording waits'))}</span></div>
      <p class="settings-hint">${escapeHtml(t('recordingTiming.hint', 'These limits control how long the server waits for real stream data. They do not change quality checks or fill missing samples. Each session records the values it used.'))}</p>
      <div class="field"><label for="recording-start-wait">${escapeHtml(t('recordingTiming.start', 'Wait at start (seconds)'))}</label>
        <input type="number" id="recording-start-wait" min="1" max="30" step="1" data-recording-start-wait></div>
      <div class="field"><label for="recording-end-wait">${escapeHtml(t('recordingTiming.end', 'Wait at end (seconds)'))}</label>
        <input type="number" id="recording-end-wait" min="1" max="30" step="1" data-recording-end-wait></div>
      <div class="dashboard-actions"><button class="btn-primary" type="button" data-save-recording-timing><i class="iconoir-floppy-disk"></i> <span>${escapeHtml(t('recordingTiming.save', 'Save recording waits'))}</span></button></div>
      <div class="settings-hint" data-recording-timing-message role="status"></div>
    </article>`;
}

export function bindRecordingTimingPanel(root, showToast) {
  root?.querySelector('[data-save-recording-timing]')?.addEventListener('click', () => {
    void saveRecordingTimingPanel(showToast);
  });
}

export async function refreshRecordingTimingPanel() {
  const panel = document.getElementById('recording-timing-panel');
  if (!panel) return;
  const message = panel.querySelector('[data-recording-timing-message]');
  try {
    const result = await getJson(ENDPOINT);
    if (!panel.isConnected) return;
    panel.dataset.revision = result.revision || '';
    panel.querySelector('[data-recording-start-wait]').value = result.settings.start_wait_seconds;
    panel.querySelector('[data-recording-end-wait]').value = result.settings.end_tail_wait_seconds;
    message.textContent = '';
  } catch (error) {
    if (panel.isConnected) message.textContent = error.message;
  }
}

async function saveRecordingTimingPanel(showToast) {
  const panel = document.getElementById('recording-timing-panel');
  if (!panel) return;
  const message = panel.querySelector('[data-recording-timing-message]');
  const start = Number(panel.querySelector('[data-recording-start-wait]').value);
  const end = Number(panel.querySelector('[data-recording-end-wait]').value);
  if (![start, end].every((value) => Number.isFinite(value) && value >= 1 && value <= 30)) {
    message.textContent = t('recordingTiming.invalid', 'Enter a wait between 1 and 30 seconds.');
    showToast?.(message.textContent, 'error');
    return;
  }
  try {
    const result = await postJson(ENDPOINT, {
      revision: panel.dataset.revision,
      settings: { start_wait_seconds: start, end_tail_wait_seconds: end },
    });
    panel.dataset.revision = result.revision || '';
    message.textContent = t('recordingTiming.saved', 'Recording waits saved. New sessions use these values.');
    showToast?.(message.textContent, 'success');
  } catch (error) {
    message.textContent = error.message;
    showToast?.(t('pluginSettings.saveFailed', 'Could not save.'), 'error');
  }
}
