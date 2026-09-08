/**
 * Persistent finalization monitor.
 *
 * Local commit, recording freeze, XDF validation/merge, statistics and both
 * destinations are one durable state machine.  Legacy upload jobs remain a
 * fallback for sessions created by an older Study Runner version.
 */
import { getJson, postJson } from '../shared/api-client.js';
import { t } from '../shared/i18n.js';
import { escapeHtml, formatDateTime } from '../shared/dom-utils.js';
import {
  finalizationProgress,
  finalizationSessionKey,
  pickFinalizationFocus,
} from '../shared/finalization-view-model.js';
import { createModal } from '../shared/modal.js';
import { renderFinalizationJob } from './finalization-monitor-view.js';

const POLL_INTERVAL_MS = 3000;
let callbacks = {};
let initialized = false;
let modal = null;
let widget = null;
let pollTimer = null;
let knownFinalizationStatuses = null;
let knownUploadSessionIds = null;
let knownCompletionIds = null;
let focusSession = null;
let modalDismissed = true;
let modalSessionKey = null;
let modalRenderSignature = null;
let latestFinalizationJobs = new Map();
let finalizationDetails = new Map();
const knownFinalizationSessionIds = new Set();

export function initializeUploadMonitor(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;

  modal = createModal({
    title: t('finalization.title', 'Finalization in progress'),
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
    <i class="iconoir-refresh" data-finalization-widget-icon></i>
    <div class="upload-widget-body">
      <div class="upload-widget-title" id="upload-widget-title" aria-live="polite"></div>
      <div class="upload-progress" id="upload-widget-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="upload-progress-fill" id="upload-widget-fill"></div></div>
    </div>
  `;
  el.addEventListener('click', () => {
    if (focusSession) openModal(focusSession);
  });
  return el;
}

async function poll() {
  const [finalizationStatus, uploadStatus, completedSessions] = await Promise.all([
    loadStatus('/api/finalization/status', 'finalization'),
    loadStatus('/api/uploads/status', 'uploads'),
    loadStatus('/api/admin/sessions', 'completed sessions'),
  ]);
  if (!finalizationStatus && !uploadStatus && !completedSessions) return;

  const finalizationJobs = (Array.isArray(finalizationStatus?.jobs) ? finalizationStatus.jobs : [])
    .map(finalizationSession);
  latestFinalizationJobs = new Map(finalizationJobs.map((job) => [job.job_id, job]));
  finalizationJobs.forEach((job) => knownFinalizationSessionIds.add(sessionIdentityKey(job)));
  const uploadSessions = Array.isArray(uploadStatus?.sessions) ? uploadStatus.sessions : [];
  const localSessions = Array.isArray(completedSessions) ? completedSessions : [];
  const legacySessions = mergeLegacySessions(
    uploadSessions.filter((session) => !knownFinalizationSessionIds.has(sessionIdentityKey(session))),
    localSessions.filter((session) => !knownFinalizationSessionIds.has(sessionIdentityKey(session))),
  );
  const sessions = [...finalizationJobs, ...legacySessions];

  const finalizationWasReady = knownFinalizationStatuses !== null;
  if (!finalizationWasReady && finalizationStatus) {
    // Establish a quiet baseline: reopening the admin page must not surface
    // every historical session in a modal. Each endpoint establishes its own
    // baseline so one temporarily unavailable legacy endpoint cannot replay
    // historical finalizations later.
    knownFinalizationStatuses = new Map(finalizationJobs.map((job) => [job.job_id, job.status]));
  } else if (finalizationWasReady && finalizationStatus) {
    surfaceFinalizationChanges(finalizationJobs);
  }

  const legacyWasReady = knownUploadSessionIds !== null && knownCompletionIds !== null;
  if (knownUploadSessionIds === null && uploadStatus) {
    knownUploadSessionIds = new Set(uploadSessions.map((session) => session.session_id));
  }
  if (knownCompletionIds === null && completedSessions) {
    knownCompletionIds = new Set(localSessions.map(completionId));
  }
  if (legacyWasReady && uploadStatus && completedSessions) {
    surfaceLegacyChanges(uploadSessions, localSessions);
  }

  focusSession = pickFinalizationFocus(finalizationJobs) || pickLegacyFocus(legacySessions);

  if (modal.isOpen() && modalSessionKey) {
    const shown = sessions.find((session) => session.session_key === modalSessionKey);
    if (shown) {
      renderModalBody(shown);
      if (shown.is_finalization) void refreshFinalizationDetails(shown.job_id);
    }
  }
  renderWidget();
}

async function loadStatus(url, label) {
  try {
    return await getJson(url, { timeoutMs: 1500 });
  } catch (error) {
    console.error(`[finalization] Could not load ${label}:`, error);
    return null;
  }
}

function surfaceFinalizationChanges(jobs) {
  const fresh = jobs.find((job) => !knownFinalizationStatuses.has(job.job_id));
  const newlyAttentionRequired = jobs.find((job) => (
    knownFinalizationStatuses.has(job.job_id)
    && knownFinalizationStatuses.get(job.job_id) !== 'attention_required'
    && job.status === 'attention_required'
  ));
  const newlyCompleted = jobs.filter((job) => {
    const before = knownFinalizationStatuses.get(job.job_id);
    return before && !['completed', 'completed_degraded'].includes(before)
      && ['completed', 'completed_degraded'].includes(job.status);
  });

  if (newlyAttentionRequired || fresh) openModal(newlyAttentionRequired || fresh);
  newlyCompleted.forEach((job) => callbacks.onLocalCompletion?.(job));
  jobs.forEach((job) => knownFinalizationStatuses.set(job.job_id, job.status));
}

function surfaceLegacyChanges(uploadSessions, localSessions) {
  const freshLocal = localSessions.find((session) => !knownCompletionIds.has(completionId(session)));
  if (freshLocal) {
    const uploadSession = uploadSessions.find((session) => session.session_id === freshLocal.session_id);
    openModal(localCompletionSession(freshLocal, uploadSession?.jobs || []));
    callbacks.onLocalCompletion?.(freshLocal);
  } else {
    const freshUpload = uploadSessions.find((session) => !knownUploadSessionIds.has(session.session_id));
    if (freshUpload) openModal(uploadOnlySession(freshUpload));
  }
  uploadSessions.forEach((session) => knownUploadSessionIds.add(session.session_id));
  localSessions.forEach((session) => knownCompletionIds.add(completionId(session)));
}

function finalizationSession(job) {
  const details = finalizationDetails.get(job.job_id);
  return {
    ...job,
    artifacts: details?.artifacts || job.artifacts || [],
    session_key: finalizationSessionKey(job),
    is_finalization: true,
    local_saved: true,
    created_at: job.created_at,
  };
}

function pickLegacyFocus(sessions) {
  return sessions.find((session) => (session.jobs || []).some((job) => job.status !== 'done')) || null;
}

function openModal(session) {
  modalSessionKey = session.session_key || session.session_id;
  modal.setTitle(session.is_finalization
    ? `${session.study_id} / ${session.participant_id}`
    : session.local_saved
      ? `${t('uploads.completionTitle', 'Study saved')}: ${session.study_id} / ${session.participant_id}`
      : `${session.study_id} / ${session.participant_id}`);
  modalRenderSignature = null;
  renderModalBody(session, { force: true });
  modal.open();
  modalDismissed = false;
  renderWidget();
  if (session.is_finalization) void refreshFinalizationDetails(session.job_id, { force: true });
}

function renderModalBody(session, { force = false } = {}) {
  const signature = JSON.stringify(session);
  if (!force && signature === modalRenderSignature) return;
  modalRenderSignature = signature;
  if (session.is_finalization) {
    renderFinalizationBody(session);
  } else {
    renderLegacyBody(session);
  }
}

function renderFinalizationBody(job) {
  renderFinalizationJob(modal.body, job, {
    onRetry: retryFinalizationStep,
    onConfirmDegraded: confirmDegraded,
    onOpenFolder: openFinalizationFolder,
  });
}

async function refreshFinalizationDetails(jobId, { force = false } = {}) {
  const current = latestFinalizationJobs.get(jobId);
  const cached = finalizationDetails.get(jobId);
  if (!force && cached?._status_updated_at === current?.updated_at) return;
  try {
    const payload = await getJson(`/api/finalization/${encodeURIComponent(jobId)}`, { timeoutMs: 1500 });
    const details = payload?.job;
    if (!details) return;
    finalizationDetails.set(jobId, { ...details, _status_updated_at: current?.updated_at });
    if (modal.isOpen() && modalSessionKey === finalizationSessionKey(details)) {
      renderModalBody(finalizationSession(current || details));
    }
  } catch (error) {
    console.error('[finalization] Could not load finalization details:', error);
  }
}

async function retryFinalizationStep(jobId, stepKey) {
  try {
    await postJson(`/api/finalization/${encodeURIComponent(jobId)}/retry`, { step: stepKey });
    await poll();
  } catch (error) {
    console.error('[finalization] Step retry failed:', error);
    callbacks.showToast?.(t('finalization.retryFailed', 'The finalization step could not be retried'), 'error');
  }
}

async function confirmDegraded(jobId, reason) {
  const explanation = String(reason || '').trim();
  if (!explanation) return;
  try {
    await postJson(`/api/finalization/${encodeURIComponent(jobId)}/confirm-degraded`, {
      reason: explanation,
      confirmed_by: 'admin',
    });
    callbacks.showToast?.(t('finalization.degradedConfirmed', 'Degraded completion confirmed'), 'success');
    await poll();
  } catch (error) {
    console.error('[finalization] Degraded confirmation failed:', error);
    callbacks.showToast?.(t('finalization.degradedFailed', 'Degraded completion could not be confirmed'), 'error');
  }
}

async function openFinalizationFolder(jobId) {
  try {
    await postJson(`/api/finalization/${encodeURIComponent(jobId)}/open-folder`, {});
  } catch (error) {
    console.error('[finalization] Could not open session folder:', error);
    callbacks.showToast?.(t('finalization.openFolderFailed', 'Could not open the session folder'), 'error');
  }
}

function renderLegacyBody(session) {
  const jobs = session.jobs || [];
  const metadata = session.metadata || {};
  const files = Array.isArray(metadata.recorded_files) ? metadata.recorded_files : [];
  const localSavedRow = session.local_saved ? `
    <div class="upload-job-row">
      <i class="iconoir-check-circle upload-job-icon--done"></i>
      <div class="upload-job-body">
        <div class="upload-job-label">${escapeHtml(t('uploads.localSaved', 'Saved locally'))}</div>
        <div class="upload-job-status">${escapeHtml(t('uploads.localSavedBody', 'The result is already saved on this computer.'))}</div>
      </div>
    </div>` : '';
  const emptyUploadRow = session.local_saved && !jobs.length ? `
    <div class="upload-job-row">
      <i class="iconoir-info-circle"></i>
      <div class="upload-job-body">
        <div class="upload-job-label">${escapeHtml(t('uploads.kicker', 'Background upload'))}</div>
        <div class="upload-job-status">${escapeHtml(t('uploads.noUploadDestinations', 'No background upload jobs were created for this study.'))}</div>
      </div>
    </div>` : '';

  modal.body.innerHTML = `
    <p class="settings-hint">${escapeHtml(t('uploads.recordedSummary', '{count} answers recorded at {time}.')
      .replace('{count}', String(metadata.answer_count ?? '-'))
      .replace('{time}', formatDateTime(session.created_at)))}</p>
    ${files.length ? `<div class="upload-file-list">${files.map((file) => `<span class="upload-file-chip">${escapeHtml(String(file).split(/[\\/]/).pop())}</span>`).join('')}</div>` : ''}
    <div class="upload-job-list">${localSavedRow}${jobs.map(renderLegacyJobRow).join('')}${emptyUploadRow}</div>
    <div class="dashboard-actions finalization-actions">
      <button class="btn-secondary" type="button" data-action="open-files"><i class="iconoir-folder"></i> ${escapeHtml(t('uploads.openFiles', 'Open files'))}</button>
    </div>`;

  modal.body.querySelectorAll('[data-retry-job]').forEach((button) => {
    button.addEventListener('click', () => void retryLegacyJob(button.dataset.retryJob));
  });
  modal.body.querySelector('[data-action="open-files"]')?.addEventListener('click', () => {
    void openLegacyResultsFolder(session.study_id, session.participant_id);
  });
}

function mergeLegacySessions(uploadSessions, localSessions) {
  const uploadsBySessionId = new Map(uploadSessions.map((session) => [session.session_id, session]));
  const localSessionIds = new Set();
  const localItems = localSessions.map((session) => {
    localSessionIds.add(session.session_id);
    return localCompletionSession(session, uploadsBySessionId.get(session.session_id)?.jobs || []);
  });
  return [
    ...localItems,
    ...uploadSessions.filter((session) => !localSessionIds.has(session.session_id)).map(uploadOnlySession),
  ];
}

function localCompletionSession(session, jobs = []) {
  const files = Array.isArray(session.files)
    ? session.files.map((file) => file.name || file.path || file).filter(Boolean)
    : [];
  return {
    session_key: completionId(session),
    session_id: session.session_id,
    study_id: session.study_id,
    participant_id: session.participant_id,
    created_at: session.saved_at,
    local_saved: true,
    jobs,
    metadata: { answer_count: session.answers_count, recorded_files: files },
  };
}

function uploadOnlySession(session) {
  return { ...session, session_key: `upload:${session.session_id}`, local_saved: false };
}

function completionId(session) {
  return [session.study_id || '', session.participant_id || '', session.session_id || '', session.result_file || ''].join('::');
}

function sessionIdentityKey(session) {
  return [session.study_id || '', session.participant_id || '', session.session_id || ''].join('::');
}

function renderLegacyJobRow(job) {
  // The destination plugin supplies its display label when it creates the
  // durable job. Unknown/new destinations therefore render without a core map.
  const label = job.label || job.kind;
  const { icon, text } = legacyJobStatusDisplay(job);
  const retryButton = job.status === 'failed'
    ? `<button class="btn-icon-only" type="button" data-retry-job="${escapeHtml(job.job_id)}" title="${escapeHtml(t('uploads.retry', 'Retry'))}" aria-label="${escapeHtml(t('uploads.retry', 'Retry'))}"><i class="iconoir-refresh"></i></button>`
    : '';
  const errorLine = job.status === 'failed' && job.last_error
    ? `<div class="upload-job-error">${escapeHtml(job.last_error)}</div>` : '';
  return `
    <div class="upload-job-row">
      <i class="${icon}"></i>
      <div class="upload-job-body"><div class="upload-job-label">${escapeHtml(label)}</div><div class="upload-job-status">${escapeHtml(text)}</div>${errorLine}</div>
      ${retryButton}
    </div>`;
}

function legacyJobStatusDisplay(job) {
  switch (job.status) {
    case 'done': return { icon: 'iconoir-check-circle upload-job-icon--done', text: t('uploads.statusDone', 'Uploaded') };
    case 'running': return { icon: 'iconoir-refresh upload-job-icon--running', text: t('uploads.statusRunning', 'Uploading ...') };
    case 'failed': return { icon: 'iconoir-xmark-circle upload-job-icon--failed', text: t('uploads.statusFailed', 'Failed - will retry automatically') };
    default: return { icon: 'iconoir-clock upload-job-icon--queued', text: t('uploads.statusQueued', 'Waiting to upload') };
  }
}

async function retryLegacyJob(jobId) {
  try {
    await postJson('/api/uploads/retry', { job_id: jobId });
    await poll();
  } catch (error) {
    console.error('[uploads] Retry failed:', error);
    callbacks.showToast?.(t('uploads.retryFailed', 'Retry failed'), 'error');
  }
}

async function openLegacyResultsFolder(studyId, participantId) {
  try {
    await postJson('/api/admin/system/open-results-folder', { study_id: studyId, participant_id: participantId });
  } catch (error) {
    console.error('[uploads] Could not open results folder:', error);
    callbacks.showToast?.(t('uploads.openFilesFailed', 'Could not open the results folder'), 'error');
  }
}

function renderWidget() {
  if (!widget) return;
  const showModalInstead = modal.isOpen() && !modalDismissed;
  if (!focusSession || showModalInstead) {
    widget.hidden = true;
    return;
  }

  let done;
  let total;
  let percent;
  let needsAttention;
  let title;
  if (focusSession.is_finalization) {
    const progress = finalizationProgress(focusSession);
    ({ done, total, percent } = progress);
    needsAttention = focusSession.status === 'attention_required'
      || (focusSession.steps || []).some((step) => step.status === 'failed');
    title = needsAttention
      ? t('finalization.widgetAttention', 'Finalization needs attention')
      : t('finalization.widgetProgress', 'Finalizing {done}/{total}').replace('{done}', String(done)).replace('{total}', String(total));
  } else {
    const jobs = focusSession.jobs || [];
    done = jobs.filter((job) => job.status === 'done').length;
    total = jobs.length || 1;
    percent = Math.round((done / total) * 100);
    needsAttention = jobs.some((job) => job.status === 'failed');
    title = needsAttention
      ? t('uploads.widgetFailed', 'Upload needs attention')
      : t('uploads.widgetProgress', 'Uploading {done}/{total}').replace('{done}', String(done)).replace('{total}', String(total));
  }

  widget.hidden = false;
  widget.classList.toggle('upload-widget--failed', needsAttention);
  widget.setAttribute('aria-label', title);
  const iconEl = widget.querySelector('[data-finalization-widget-icon]');
  if (iconEl) iconEl.className = needsAttention ? 'iconoir-warning-triangle' : 'iconoir-refresh';
  const titleEl = widget.querySelector('#upload-widget-title');
  if (titleEl) titleEl.textContent = title;
  const progressEl = widget.querySelector('#upload-widget-progress');
  if (progressEl) {
    progressEl.setAttribute('aria-label', title);
    progressEl.setAttribute('aria-valuenow', String(percent));
  }
  const fillEl = widget.querySelector('#upload-widget-fill');
  if (fillEl) fillEl.style.width = `${percent}%`;
}
