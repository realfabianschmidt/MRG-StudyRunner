/**
 * Background-upload monitor: a completion modal for a session that just
 * finished, shrinking to a small corner widget once closed.
 *
 * The participant's submit already returned instantly - this only reports
 * what the background upload jobs (see upload_jobs_service.py) are doing.
 * Polling starts from a clean baseline so sessions that already existed
 * when the admin page loaded do not pop up a modal by themselves; only a
 * session that appears while the operator is watching does.
 */
import { getJson, postJson } from '../api-client.js';
import { t } from '../i18n.js';
import { escapeHtml, formatDateTime } from '../lib/dom-utils.js';
import { createModal } from '../lib/modal.js';

const POLL_INTERVAL_MS = 3000;
const DESTINATION_LABELS = {
  notion: () => t('uploads.destinationNotion', 'Notion'),
  nextcloud: () => t('uploads.destinationNextcloud', 'Nextcloud'),
};

let callbacks = {};
let initialized = false;
let modal = null;
let widget = null;
let pollTimer = null;
let knownSessionIds = null;
let focusSession = null;
let modalDismissed = true;
let modalSessionId = null;

export function initializeUploadMonitor(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;

  modal = createModal({
    kicker: t('uploads.kicker', 'Background upload'),
    title: t('uploads.title', 'Upload in progress'),
    closeLabel: t('settings.close', 'Close'),
    onClose: () => {
      modalDismissed = true;
      renderWidget();
    },
  });

  widget = buildWidget();
  document.body.appendChild(widget);

  void poll();
  pollTimer = setInterval(() => void poll(), POLL_INTERVAL_MS);
}

function buildWidget() {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'upload-widget';
  el.hidden = true;
  el.innerHTML = `
    <i class="iconoir-cloud-upload"></i>
    <div class="upload-widget-body">
      <div class="upload-widget-title" id="upload-widget-title"></div>
      <div class="upload-progress"><div class="upload-progress-fill" id="upload-widget-fill"></div></div>
    </div>
  `;
  el.addEventListener('click', () => {
    if (focusSession) openModal(focusSession);
  });
  return el;
}

async function poll() {
  let status;
  try {
    status = await getJson('/api/uploads/status');
  } catch (error) {
    console.error('[uploads] Could not load upload status:', error);
    return;
  }

  const sessions = Array.isArray(status?.sessions) ? status.sessions : [];

  if (knownSessionIds === null) {
    // First poll: establish the baseline without surfacing a modal for work
    // that was already queued before this admin page was opened.
    knownSessionIds = new Set(sessions.map((session) => session.session_id));
  } else {
    const freshSession = sessions.find((session) => !knownSessionIds.has(session.session_id));
    if (freshSession) {
      knownSessionIds.add(freshSession.session_id);
      openModal(freshSession);
    }
    sessions.forEach((session) => knownSessionIds.add(session.session_id));
  }

  focusSession = pickFocusSession(sessions);

  if (modal.isOpen() && modalSessionId) {
    // Keep an open modal live as job statuses change (queued -> running -> done).
    const shown = sessions.find((session) => session.session_id === modalSessionId);
    if (shown) renderModalBody(shown);
  }

  renderWidget();
}

function pickFocusSession(sessions) {
  return sessions.find((session) => (session.jobs || []).some((job) => job.status !== 'done')) || null;
}

function openModal(session) {
  modalSessionId = session.session_id;
  modal.setTitle(`${session.study_id} / ${session.participant_id}`);
  renderModalBody(session);
  modal.open();
  modalDismissed = false;
  renderWidget();
}

function renderModalBody(session) {
  const jobs = session.jobs || [];
  const metadata = session.metadata || {};
  const files = Array.isArray(metadata.recorded_files) ? metadata.recorded_files : [];

  modal.body.innerHTML = `
    <p class="settings-hint" style="margin-bottom: 12px;">
      ${escapeHtml(t('uploads.recordedSummary', '{count} answers recorded at {time}.')
        .replace('{count}', String(metadata.answer_count ?? '-'))
        .replace('{time}', formatDateTime(session.created_at)))}
    </p>
    ${files.length ? `<div class="upload-file-list">${files.map((file) => `<span class="upload-file-chip">${escapeHtml(String(file).split(/[\\/]/).pop())}</span>`).join('')}</div>` : ''}
    <div class="upload-job-list">
      ${jobs.map(renderJobRow).join('')}
    </div>
    <div class="dashboard-actions" style="margin-top: 14px;">
      <button class="btn-secondary" type="button" data-action="open-files">
        <i class="iconoir-folder"></i> ${escapeHtml(t('uploads.openFiles', 'Open files'))}
      </button>
    </div>
  `;

  modal.body.querySelectorAll('[data-retry-job]').forEach((button) => {
    button.addEventListener('click', () => void retryJob(button.dataset.retryJob));
  });
  modal.body.querySelector('[data-action="open-files"]')?.addEventListener('click', () => {
    void openResultsFolder(session.study_id, session.participant_id);
  });
}

function renderJobRow(job) {
  const label = DESTINATION_LABELS[job.kind]?.() || job.label || job.kind;
  const { icon, text } = jobStatusDisplay(job);
  const retryButton = job.status === 'failed'
    ? `<button class="btn-icon-only" type="button" data-retry-job="${escapeHtml(job.job_id)}" title="${escapeHtml(t('uploads.retry', 'Retry'))}" aria-label="${escapeHtml(t('uploads.retry', 'Retry'))}"><i class="iconoir-refresh"></i></button>`
    : '';
  const errorLine = job.status === 'failed' && job.last_error
    ? `<div class="upload-job-error">${escapeHtml(job.last_error)}</div>`
    : '';
  return `
    <div class="upload-job-row">
      <i class="${icon}"></i>
      <div class="upload-job-body">
        <div class="upload-job-label">${escapeHtml(label)}</div>
        <div class="upload-job-status">${escapeHtml(text)}</div>
        ${errorLine}
      </div>
      ${retryButton}
    </div>`;
}

function jobStatusDisplay(job) {
  switch (job.status) {
    case 'done':
      return { icon: 'iconoir-check-circle upload-job-icon--done', text: t('uploads.statusDone', 'Uploaded') };
    case 'running':
      return { icon: 'iconoir-refresh upload-job-icon--running', text: t('uploads.statusRunning', 'Uploading ...') };
    case 'failed':
      return { icon: 'iconoir-xmark-circle upload-job-icon--failed', text: t('uploads.statusFailed', 'Failed - will retry automatically') };
    default:
      return { icon: 'iconoir-clock upload-job-icon--queued', text: t('uploads.statusQueued', 'Waiting to upload') };
  }
}

async function retryJob(jobId) {
  try {
    await postJson('/api/uploads/retry', { job_id: jobId });
    await poll();
  } catch (error) {
    console.error('[uploads] Retry failed:', error);
    callbacks.showToast?.(t('uploads.retryFailed', 'Retry failed'), 'error');
  }
}

async function openResultsFolder(studyId, participantId) {
  try {
    await postJson('/api/admin/system/open-results-folder', { study_id: studyId, participant_id: participantId });
  } catch (error) {
    console.error('[uploads] Could not open results folder:', error);
    callbacks.showToast?.(t('uploads.openFilesFailed', 'Could not open the results folder'), 'error');
  }
}

function renderWidget() {
  if (!widget) return;
  // The widget stays out of the way while its own session's modal is open.
  const showModalInstead = modal.isOpen() && !modalDismissed;
  if (!focusSession || showModalInstead) {
    widget.hidden = true;
    return;
  }

  const jobs = focusSession.jobs || [];
  const done = jobs.filter((job) => job.status === 'done').length;
  const total = jobs.length || 1;
  const hasFailed = jobs.some((job) => job.status === 'failed');

  widget.hidden = false;
  widget.classList.toggle('upload-widget--failed', hasFailed);
  const titleEl = widget.querySelector('#upload-widget-title');
  if (titleEl) {
    titleEl.textContent = hasFailed
      ? t('uploads.widgetFailed', 'Upload needs attention')
      : t('uploads.widgetProgress', 'Uploading {done}/{total}').replace('{done}', String(done)).replace('{total}', String(total));
  }
  const fillEl = widget.querySelector('#upload-widget-fill');
  if (fillEl) fillEl.style.width = `${Math.round((done / total) * 100)}%`;
}
