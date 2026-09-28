/**
 * The data folder settings page.
 *
 * Studies, results, settings, credentials, logos and the iPad certificate live
 * in one folder. By default that is inside the program folder; here the
 * operator can move to another folder (another drive, an external disk), link
 * an existing Study Runner data folder again after a reinstall, or go back to
 * the default. Every change restarts Study Runner.
 */
import { t } from '../../shared/i18n.js';
import { escapeHtml } from '../../shared/dom-utils.js';
import { getJson, postJson } from '../../shared/api-client.js';

const PANEL_ROOT_ID = 'data-folder-settings-panel';

let callbacks = {};
let status = null;
let candidate = { path: '', kind: '' };

export function initializeDataFolderSettings(options = {}) {
  callbacks = options;
}

/** Markup for the shell. Data arrives later through refreshDataFolderSettings(). */
export function renderDataFolderSettingsPanel() {
  return `
    <article class="dashboard-card dashboard-card--wide">
      <div class="dashboard-card-title">
        <i class="iconoir-folder"></i> <span>${escapeHtml(t('dataFolder.title', 'Data folder'))}</span>
      </div>
      <p class="settings-hint">${escapeHtml(t('dataFolder.subtitle', 'Studies, results, settings, credentials, logos and the iPad certificate are kept together in one folder. It can live outside the program, for example on an external drive.'))}</p>
      <div id="${PANEL_ROOT_ID}"></div>
    </article>`;
}

/** Called by the shell when the panel is shown. */
export async function refreshDataFolderSettings() {
  try {
    status = await getJson('/api/admin/data-folder', { timeoutMs: 3000 });
  } catch (error) {
    status = { error: String(error.message || error) };
  }
  render();
}

function render() {
  const root = document.getElementById(PANEL_ROOT_ID);
  if (!root || !status) return;
  if (status.error) {
    root.innerHTML = `<p class="settings-hint">${escapeHtml(status.error)}</p>`;
    return;
  }

  const current = status.external
    ? status.current
    : t('dataFolder.inProgram', 'Inside the program folder ({path})').replace('{path}', status.program_folder || '');
  const locked = Boolean(status.set_by_environment);
  root.innerHTML = `
    <div class="status-card">
      <div class="status-card-label">${escapeHtml(t('dataFolder.current', 'Current data folder'))}</div>
      <div class="status-card-value">${escapeHtml(current)}</div>
    </div>
    ${locked ? `<p class="settings-hint">${escapeHtml(t('dataFolder.setByEnvironment', 'Set by STUDY_RUNNER_DATA_DIR; change it there.'))}</p>` : `
    ${renderRemembered()}
    <div class="field">
      <label for="data-folder-path">${escapeHtml(t('dataFolder.newLabel', 'Other data folder'))}</label>
      <div class="dashboard-actions">
        <input type="text" id="data-folder-path" value="${escapeHtml(candidate.path)}" placeholder="${escapeHtml(t('dataFolder.placeholder', 'e.g. D:\\StudyRunnerData or /Volumes/Lab/StudyRunner'))}">
        <button class="btn-secondary" type="button" data-data-folder-choose>
          <i class="iconoir-folder"></i> <span>${escapeHtml(t('dataFolder.choose', 'Choose folder ...'))}</span>
        </button>
      </div>
    </div>
    <label class="switch-row" for="data-folder-copy">
      <span>${escapeHtml(t('dataFolder.copyCurrent', 'Bring the current data along'))}</span>
      <span class="switch"><input type="checkbox" id="data-folder-copy"><span class="switch-slider"></span></span>
    </label>
    <p class="settings-hint">${escapeHtml(t('dataFolder.hint', 'An empty folder is set up like a clean install (new iPad certificate - tablets must trust it once). With "Bring the current data along" everything is copied first; the old copy stays where it is. An existing Study Runner data folder is linked as it is.'))}</p>
    <div class="dashboard-actions">
      <button class="btn-primary" type="button" data-data-folder-apply>
        <i class="iconoir-check"></i> <span>${escapeHtml(t('dataFolder.apply', 'Use and restart'))}</span>
      </button>
      ${status.external ? `<button class="btn-secondary" type="button" data-data-folder-reset>
        <i class="iconoir-undo"></i> <span>${escapeHtml(t('dataFolder.reset', 'Back to the program folder'))}</span>
      </button>` : ''}
    </div>`}`;

  root.querySelector('[data-data-folder-choose]')?.addEventListener('click', () => void chooseFolder());
  root.querySelector('[data-data-folder-apply]')?.addEventListener('click', () => {
    void applyFolder({ path: root.querySelector('#data-folder-path')?.value || '', copy: root.querySelector('#data-folder-copy')?.checked });
  });
  root.querySelector('[data-data-folder-reset]')?.addEventListener('click', () => void applyFolder({ reset: true }));
  root.querySelectorAll('[data-data-folder-link]').forEach((button) => {
    button.addEventListener('click', () => void applyFolder({ path: button.dataset.dataFolderLink }));
  });
}

/** After a reinstall: data folders this user used before, one click to link. */
function renderRemembered() {
  const folders = status.remembered || [];
  if (!folders.length) return '';
  return `
    <div class="field">
      <label>${escapeHtml(t('dataFolder.rememberedLabel', 'Found from an earlier installation'))}</label>
      ${folders.map((folder) => `
        <div class="dashboard-actions">
          <span class="status-card-value">${escapeHtml(folder)}</span>
          <button class="btn-secondary" type="button" data-data-folder-link="${escapeHtml(folder)}">
            <i class="iconoir-link"></i> <span>${escapeHtml(t('dataFolder.link', 'Link and restart'))}</span>
          </button>
        </div>`).join('')}
    </div>`;
}

async function chooseFolder() {
  try {
    const result = await postJson('/api/admin/data-folder/choose', {});
    if (!result.path) return;
    candidate = { path: result.path, kind: result.kind || '' };
    render();
  } catch (error) {
    candidate = { path: error.payload?.path || candidate.path, kind: '' };
    render();
    callbacks.showToast?.(String(error.message || error), 'error');
  }
}

async function applyFolder({ path = '', copy = false, reset = false }) {
  const message = reset
    ? t('dataFolder.resetConfirm', 'Use the folder inside the program again? The data in the current folder stays where it is. Study Runner restarts.')
    : t('dataFolder.applyConfirm', 'Use {path} as the data folder? Study Runner restarts.').replace('{path}', path);
  const confirmed = await callbacks.confirmWithModal?.({
    title: t('dataFolder.title', 'Data folder'),
    message,
    confirmLabel: t('dataFolder.confirm', 'Restart'),
  });
  if (confirmed === false) return;
  try {
    await postJson('/api/admin/data-folder', reset ? { reset: true } : { path, copy_current: Boolean(copy) });
    callbacks.showToast?.(t('dataFolder.restarting', 'Study Runner is restarting with the new data folder ...'), 'success');
    await callbacks.waitForRestartAndReload?.();
  } catch (error) {
    callbacks.showToast?.(String(error.message || error), 'error');
  }
}
